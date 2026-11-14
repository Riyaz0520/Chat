import html
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone

import bleach
from bson import ObjectId
from bson.errors import InvalidId
from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError, ServerSelectionTimeoutError
import requests
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = "91a86a0af6b6a5063f40982bd724f32d1d8ff272eca50f85610da6a7713041e2"
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

MONGO_URI = "mongodb+srv://shaikriyaz1820_db_user:Riyaz0520@cluster0.q5xlrt7.mongodb.net/"
MONGO_DB = "tharik_forum"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"
GEMINI_API_KEY = "AQ.Ab8RN6KZF9I8pMeZLB0oJ3kZF5QksMWdZ3LiOZLAbSPCMQlE7w"
GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
CHAT_RATE_LIMIT = 20
CHAT_HISTORY_LIMIT = 12

mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
db = mongo_client[MONGO_DB]
users_col = db["users"]
categories_col = db["categories"]
topics_col = db["topics"]
comments_col = db["comments"]
replies_col = db["replies"]
forum_views_col = db["forum_views"]
chat_messages_col = db["chat_messages"]

_db_lock = threading.Lock()
_db_ready = False

ALLOWED_TAGS = [
    "a", "b", "blockquote", "br", "code", "div", "em", "h1", "h2", "h3", "h4",
    "i", "img", "li", "ol", "p", "pre", "s", "span", "strong", "u", "ul",
]
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title", "width", "height"],
    "span": ["class"],
    "div": ["class"],
    "p": ["class"],
}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]
USER_TYPES = {1: "Admin", 2: "Staff", 3: "Member"}
CHAT_SYSTEM_PROMPT = (
    "You are Tharik Assistant, a helpful assistant inside Tharik Forum. "
    "Answer every question clearly, accurately, and concisely. If the user asks "
    "about the forum, explain how to create topics, comment, reply, search, or "
    "manage categories/users when relevant. For general questions, answer helpfully."
)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def object_id(value):
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError, ValueError):
        return None


def serialize_value(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, list):
        return [serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize_value(item) for key, item in value.items()}
    return value


def serialize_doc(doc):
    return serialize_value(doc) if doc else None


def clean_html(value):
    return bleach.clean(
        value or "",
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )


def html_to_text(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text


def format_dt(value, empty="-"):
    if not value:
        return empty
    if isinstance(value, datetime):
        return value.strftime("%b %d, %Y %I:%M %p")
    return str(value)


def excerpt(value, limit=180):
    text = html_to_text(value)
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def current_user():
    return session.get("user")


def user_id():
    user = current_user()
    return object_id(user.get("id")) if user else None


def is_admin(user=None):
    user = user or current_user()
    return bool(user and int(user.get("type", 0)) == 1)


def login_required(view):
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    wrapped.__name__ = view.__name__
    return wrapped


def admin_required(view):
    @login_required
    def wrapped(*args, **kwargs):
        if not is_admin():
            abort(403)
        return view(*args, **kwargs)
    wrapped.__name__ = view.__name__
    return wrapped


def can_manage(item):
    user = current_user()
    return bool(user and (str(item.get("user_id")) == user.get("id") or is_admin(user)))


def ensure_db():
    global _db_ready
    if _db_ready:
        return
    with _db_lock:
        if _db_ready:
            return
        users_col.create_index("username_key", unique=True)
        categories_col.create_index("name", unique=True)
        topics_col.create_index([("created_at", DESCENDING)])
        topics_col.create_index("category_ids")
        comments_col.create_index([("topic_id", ASCENDING), ("created_at", ASCENDING)])
        replies_col.create_index([("comment_id", ASCENDING), ("created_at", ASCENDING)])
        forum_views_col.create_index([("topic_id", ASCENDING), ("user_id", ASCENDING)], unique=True)
        forum_views_col.create_index("topic_id")
        chat_messages_col.create_index([("user_id", ASCENDING), ("created_at", ASCENDING)])
        ensure_default_admin()
        _db_ready = True


def ensure_default_admin():
    if users_col.count_documents({}) == 0:
        users_col.insert_one({
            "name": "Administrator",
            "username": ADMIN_USERNAME,
            "username_key": ADMIN_USERNAME.lower(),
            "password_hash": generate_password_hash(ADMIN_PASSWORD),
            "type": 1,
            "created_at": utcnow(),
        })
    if categories_col.count_documents({}) == 0:
        categories_col.insert_many([
            {"name": "General", "description": "General discussion for everyone.", "created_at": utcnow()},
            {"name": "Announcements", "description": "Important forum announcements.", "created_at": utcnow()},
            {"name": "Introductions", "description": "Introduce yourself to the community.", "created_at": utcnow()},
        ])


def get_categories():
    return list(categories_col.find().sort("name", ASCENDING))


def get_category_map():
    return {str(category["_id"]): category for category in get_categories()}


def get_user_map(user_ids):
    ids = [oid for oid in (object_id(value) for value in user_ids) if oid]
    if not ids:
        return {}
    users = users_col.find({"_id": {"$in": ids}})
    return {str(user["_id"]): user for user in users}


def topic_counts(topic_ids):
    counts = {"comments": {}, "replies": {}, "views": {}}
    if not topic_ids:
        return counts

    pipeline = [
        {"$match": {"topic_id": {"$in": topic_ids}}},
        {"$group": {"_id": "$topic_id", "count": {"$sum": 1}}},
    ]
    counts["comments"] = {str(row["_id"]): row["count"] for row in comments_col.aggregate(pipeline)}
    counts["views"] = {str(row["_id"]): row["count"] for row in forum_views_col.aggregate(pipeline)}

    comments = list(comments_col.find({"topic_id": {"$in": topic_ids}}, {"topic_id": 1}))
    comment_to_topic = {str(comment["_id"]): comment["topic_id"] for comment in comments}
    comment_ids = [comment["_id"] for comment in comments]
    reply_counts = {}
    if comment_ids:
        reply_pipeline = [
            {"$match": {"comment_id": {"$in": comment_ids}}},
            {"$group": {"_id": "$comment_id", "count": {"$sum": 1}}},
        ]
        for row in replies_col.aggregate(reply_pipeline):
            topic_id = comment_to_topic.get(str(row["_id"]))
            if topic_id:
                key = str(topic_id)
                reply_counts[key] = reply_counts.get(key, 0) + row["count"]
    counts["replies"] = reply_counts
    return counts


def prepare_topics(raw_topics):
    topic_ids = [topic["_id"] for topic in raw_topics]
    user_ids = [topic.get("user_id") for topic in raw_topics]
    users = get_user_map(user_ids)
    categories = get_category_map()
    counts = topic_counts(topic_ids)

    prepared = []
    for topic in raw_topics:
        data = serialize_doc(topic)
        author = users.get(str(topic.get("user_id")), {})
        topic_key = str(topic["_id"])
        category_ids = [str(value) for value in topic.get("category_ids", [])]
        data.update({
            "author_name": topic.get("author_name") or author.get("name", "Unknown user"),
            "author_username": topic.get("author_username") or author.get("username", ""),
            "tags": [serialize_doc(categories[cid]) for cid in category_ids if cid in categories],
            "comment_count": counts["comments"].get(topic_key, 0),
            "reply_count": counts["replies"].get(topic_key, 0),
            "view_count": counts["views"].get(topic_key, 0),
            "created_text": format_dt(topic.get("created_at")),
            "updated_text": format_dt(topic.get("updated_at")),
            "content_text": html_to_text(topic.get("content")),
            "excerpt": excerpt(topic.get("content")),
        })
        prepared.append(data)
    return prepared


def prepare_categories(raw_categories=None):
    categories = raw_categories if raw_categories is not None else get_categories()
    return [serialize_doc(category) for category in categories]


def prepare_comments(topic_id):
    comments = list(comments_col.find({"topic_id": topic_id}).sort("created_at", ASCENDING))
    comment_ids = [comment["_id"] for comment in comments]
    replies = list(replies_col.find({"comment_id": {"$in": comment_ids}}).sort("created_at", ASCENDING)) if comment_ids else []
    replies_by_comment = {}
    for reply in replies:
        replies_by_comment.setdefault(str(reply["comment_id"]), []).append(reply)

    prepared_comments = []
    for comment in comments:
        data = serialize_doc(comment)
        data["created_text"] = format_dt(comment.get("created_at"))
        data["content_text"] = html_to_text(comment.get("content"))
        data["replies"] = []
        for reply in replies_by_comment.get(str(comment["_id"]), []):
            reply_data = serialize_doc(reply)
            reply_data["created_text"] = format_dt(reply.get("created_at"))
            reply_data["content_text"] = html_to_text(reply.get("content"))
            data["replies"].append(reply_data)
        prepared_comments.append(data)
    return prepared_comments


def get_topic_or_404(topic_id):
    oid = object_id(topic_id)
    topic = topics_col.find_one({"_id": oid}) if oid else None
    if not topic:
        abort(404)
    return topic


def record_topic_view(topic):
    user = current_user()
    uid = user_id()
    if not uid or str(topic.get("user_id")) == user.get("id"):
        return
    try:
        forum_views_col.update_one(
            {"topic_id": topic["_id"], "user_id": uid},
            {"$setOnInsert": {"created_at": utcnow()}},
            upsert=True,
        )
    except DuplicateKeyError:
        pass


def safe_next(value):
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return url_for("home")


def valid_username(username):
    return bool(re.fullmatch(r"[A-Za-z0-9_.@-]{3,80}", username or ""))


@app.context_processor
def inject_globals():
    return {
        "user": current_user(),
        "csrf_token": session.get("csrf_token", ""),
        "user_types": USER_TYPES,
        "is_admin": is_admin(),
    }


@app.before_request
def before_request():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    if request.method == "POST" and request.endpoint != "static":
        token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not token or token != session.get("csrf_token"):
            abort(400, description="Invalid CSRF token")
    if request.endpoint != "static":
        ensure_db()


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, title="Page not found", message="The page you requested does not exist."), 404


@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403, title="Access denied", message="You do not have permission to perform this action."), 403


@app.errorhandler(PyMongoError)
def mongo_error(error):
    if isinstance(error, ServerSelectionTimeoutError):
        message = "MongoDB is not reachable. Start MongoDB or check MONGO_URI in .env."
    else:
        message = "A database error occurred. Please try again later."
    return render_template("error.html", code=503, title="Database unavailable", message=message), 503


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if current_user():
        return redirect(safe_next(request.args.get("next")))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = users_col.find_one({"username_key": username.lower()}) if username else None
        if user and check_password_hash(user.get("password_hash", ""), password):
            session.clear()
            session["csrf_token"] = secrets.token_hex(16)
            session["user"] = {
                "id": str(user["_id"]),
                "name": user.get("name", ""),
                "username": user.get("username", ""),
                "type": int(user.get("type", 3)),
            }
            return redirect(safe_next(request.args.get("next")))
        error = "Username or password is incorrect."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def home():
    stats = {
        "users": users_col.count_documents({}),
        "topics": topics_col.count_documents({}),
        "categories": categories_col.count_documents({}),
        "comments": comments_col.count_documents({}),
    }
    recent_topics = prepare_topics(list(topics_col.find().sort("created_at", DESCENDING).limit(5)))
    categories = prepare_categories()
    return render_template("home.html", stats=stats, topics=recent_topics, categories=categories)


@app.route("/topics")
@login_required
def topics():
    raw_topics = list(topics_col.find().sort("created_at", DESCENDING))
    prepared = prepare_topics(raw_topics)
    categories = prepare_categories()
    return render_template("topics.html", topics=prepared, categories=categories)


@app.route("/topics/new", methods=["GET", "POST"])
@login_required
def topic_new():
    categories = prepare_categories()
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        content = clean_html(request.form.get("content"))
        category_ids = []
        seen = set()
        for value in request.form.getlist("category_ids"):
            oid = object_id(value)
            if oid and str(oid) not in seen:
                seen.add(str(oid))
                category_ids.append(oid)
        if not title or not content.strip():
            return render_template("topic_form.html", categories=categories, error="Title and content are required.", form=request.form), 400
        current = current_user()
        result = topics_col.insert_one({
            "title": title,
            "content": content,
            "category_ids": category_ids,
            "user_id": user_id(),
            "author_name": current.get("name", ""),
            "author_username": current.get("username", ""),
            "created_at": utcnow(),
            "updated_at": utcnow(),
        })
        return redirect(url_for("topic_view", topic_id=str(result.inserted_id)))
    return render_template("topic_form.html", categories=categories, topic=None)


@app.route("/topics/<topic_id>/edit", methods=["GET", "POST"])
@login_required
def topic_edit(topic_id):
    topic = get_topic_or_404(topic_id)
    if not can_manage(topic):
        abort(403)
    categories = prepare_categories()
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        content = clean_html(request.form.get("content"))
        category_ids = []
        seen = set()
        for value in request.form.getlist("category_ids"):
            oid = object_id(value)
            if oid and str(oid) not in seen:
                seen.add(str(oid))
                category_ids.append(oid)
        if not title or not content.strip():
            prepared_topic = serialize_doc(topic)
            prepared_topic["category_ids"] = [str(value) for value in topic.get("category_ids", [])]
            return render_template(
                "topic_form.html",
                categories=categories,
                topic=prepared_topic,
                error="Title and content are required.",
                form=request.form,
            ), 400
        topics_col.update_one(
            {"_id": topic["_id"]},
            {"$set": {
                "title": title,
                "content": content,
                "category_ids": category_ids,
                "updated_at": utcnow(),
            }},
        )
        return redirect(url_for("topic_view", topic_id=str(topic["_id"])))
    prepared_topic = serialize_doc(topic)
    prepared_topic["category_ids"] = [str(value) for value in topic.get("category_ids", [])]
    return render_template("topic_form.html", categories=categories, topic=prepared_topic)


@app.route("/topics/<topic_id>")
@login_required
def topic_view(topic_id):
    topic = get_topic_or_404(topic_id)
    record_topic_view(topic)
    prepared_topic = serialize_doc(topic)
    prepared_topic.update({
        "category_ids": [str(value) for value in topic.get("category_ids", [])],
        "created_text": format_dt(topic.get("created_at")),
        "updated_text": format_dt(topic.get("updated_at")),
    })
    category_map = get_category_map()
    prepared_topic["tags"] = [
        serialize_doc(category_map[cid])
        for cid in prepared_topic["category_ids"]
        if cid in category_map
    ]
    prepared_topic["view_count"] = forum_views_col.count_documents({"topic_id": topic["_id"]})
    comments = prepare_comments(topic["_id"])
    return render_template(
        "topic_view.html",
        topic=prepared_topic,
        comments=comments,
        can_manage=can_manage(topic),
    )


@app.route("/topics/<topic_id>/delete", methods=["POST"])
@login_required
def topic_delete(topic_id):
    topic = get_topic_or_404(topic_id)
    if not can_manage(topic):
        abort(403)
    comment_ids = [comment["_id"] for comment in comments_col.find({"topic_id": topic["_id"]}, {"_id": 1})]
    replies_col.delete_many({"comment_id": {"$in": comment_ids}})
    comments_col.delete_many({"topic_id": topic["_id"]})
    forum_views_col.delete_many({"topic_id": topic["_id"]})
    topics_col.delete_one({"_id": topic["_id"]})
    return redirect(url_for("topics"))


@app.route("/topics/<topic_id>/comments", methods=["POST"])
@login_required
def comment_create(topic_id):
    topic = get_topic_or_404(topic_id)
    content = clean_html(request.form.get("content"))
    if not content.strip():
        abort(400, description="Comment cannot be empty")
    current = current_user()
    comments_col.insert_one({
        "topic_id": topic["_id"],
        "user_id": user_id(),
        "author_name": current.get("name", ""),
        "author_username": current.get("username", ""),
        "content": content,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    })
    return redirect(url_for("topic_view", topic_id=str(topic["_id"])))


@app.route("/comments/<comment_id>/edit", methods=["GET", "POST"])
@login_required
def comment_edit(comment_id):
    oid = object_id(comment_id)
    comment = comments_col.find_one({"_id": oid}) if oid else None
    if not comment:
        abort(404)
    if not can_manage(comment):
        abort(403)
    if request.method == "POST":
        content = clean_html(request.form.get("content"))
        if not content.strip():
            return render_template(
                "edit_content.html",
                action=url_for("comment_edit", comment_id=str(comment["_id"])),
                label="Comment",
                name="content",
                value=comment.get("content", ""),
                error="Comment cannot be empty.",
            ), 400
        comments_col.update_one({"_id": comment["_id"]}, {"$set": {"content": content, "updated_at": utcnow()}})
        return redirect(url_for("topic_view", topic_id=str(comment["topic_id"])))
    return render_template(
        "edit_content.html",
        action=url_for("comment_edit", comment_id=str(comment["_id"])),
        label="Comment",
        name="content",
        value=comment.get("content", ""),
    )


@app.route("/comments/<comment_id>/delete", methods=["POST"])
@login_required
def comment_delete(comment_id):
    oid = object_id(comment_id)
    comment = comments_col.find_one({"_id": oid}) if oid else None
    if not comment:
        abort(404)
    if not can_manage(comment):
        abort(403)
    replies_col.delete_many({"comment_id": comment["_id"]})
    comments_col.delete_one({"_id": comment["_id"]})
    return redirect(url_for("topic_view", topic_id=str(comment["topic_id"])))


@app.route("/topics/<topic_id>/replies", methods=["POST"])
@login_required
def reply_create(topic_id):
    topic = get_topic_or_404(topic_id)
    comment_oid = object_id(request.form.get("comment_id"))
    comment = comments_col.find_one({"_id": comment_oid, "topic_id": topic["_id"]}) if comment_oid else None
    if not comment:
        abort(404)
    content = clean_html(request.form.get("content"))
    if not content.strip():
        abort(400, description="Reply cannot be empty")
    current = current_user()
    replies_col.insert_one({
        "comment_id": comment["_id"],
        "topic_id": topic["_id"],
        "user_id": user_id(),
        "author_name": current.get("name", ""),
        "author_username": current.get("username", ""),
        "content": content,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    })
    return redirect(url_for("topic_view", topic_id=str(topic["_id"])))


@app.route("/replies/<reply_id>/edit", methods=["GET", "POST"])
@login_required
def reply_edit(reply_id):
    oid = object_id(reply_id)
    reply = replies_col.find_one({"_id": oid}) if oid else None
    if not reply:
        abort(404)
    if not can_manage(reply):
        abort(403)
    if request.method == "POST":
        content = clean_html(request.form.get("content"))
        if not content.strip():
            return render_template(
                "edit_content.html",
                action=url_for("reply_edit", reply_id=str(reply["_id"])),
                label="Reply",
                name="content",
                value=reply.get("content", ""),
                error="Reply cannot be empty.",
            ), 400
        replies_col.update_one({"_id": reply["_id"]}, {"$set": {"content": content, "updated_at": utcnow()}})
        return redirect(url_for("topic_view", topic_id=str(reply.get("topic_id"))))
    return render_template(
        "edit_content.html",
        action=url_for("reply_edit", reply_id=str(reply["_id"])),
        label="Reply",
        name="content",
        value=reply.get("content", ""),
    )


@app.route("/replies/<reply_id>/delete", methods=["POST"])
@login_required
def reply_delete(reply_id):
    oid = object_id(reply_id)
    reply = replies_col.find_one({"_id": oid}) if oid else None
    if not reply:
        abort(404)
    if not can_manage(reply):
        abort(403)
    replies_col.delete_one({"_id": reply["_id"]})
    return redirect(url_for("topic_view", topic_id=str(reply.get("topic_id"))))


@app.route("/categories", methods=["GET", "POST"])
@admin_required
def categories():
    editing = None
    edit_id = request.args.get("edit")
    if edit_id:
        oid = object_id(edit_id)
        editing = categories_col.find_one({"_id": oid}) if oid else None
        if not editing:
            abort(404)
        editing = serialize_doc(editing)

    error = None
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip()
        if not name:
            error = "Category name is required."
        else:
            try:
                if editing:
                    categories_col.update_one(
                        {"_id": object_id(editing["_id"])},
                        {"$set": {"name": name, "description": description}},
                    )
                else:
                    categories_col.insert_one({
                        "name": name,
                        "description": description,
                        "created_at": utcnow(),
                    })
                return redirect(url_for("categories"))
            except DuplicateKeyError:
                error = "A category with that name already exists."
    return render_template(
        "categories.html",
        categories=prepare_categories(),
        editing=editing,
        error=error,
    )


@app.route("/categories/<category_id>/delete", methods=["POST"])
@admin_required
def category_delete(category_id):
    oid = object_id(category_id)
    category = categories_col.find_one({"_id": oid}) if oid else None
    if not category:
        abort(404)
    topics_col.update_many({}, {"$pull": {"category_ids": category["_id"]}})
    categories_col.delete_one({"_id": category["_id"]})
    return redirect(url_for("categories"))


@app.route("/users")
@admin_required
def users():
    raw_users = list(users_col.find().sort("name", ASCENDING))
    prepared = []
    for user in raw_users:
        data = serialize_doc(user)
        data["type_label"] = USER_TYPES.get(int(user.get("type", 3)), "Member")
        data["created_text"] = format_dt(user.get("created_at"))
        prepared.append(data)
    return render_template("users.html", users=prepared)


@app.route("/users/new", methods=["GET", "POST"])
@admin_required
def user_new():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        try:
            user_type = int(request.form.get("type", "2"))
        except ValueError:
            user_type = 2
        if not name or not valid_username(username) or len(password) < 8:
            return render_template(
                "user_form.html",
                account=None,
                error="Name, a valid username, and a password of at least 8 characters are required.",
                form=request.form,
            ), 400
        if users_col.find_one({"username_key": username.lower()}):
            return render_template(
                "user_form.html",
                account=None,
                error="That username is already in use.",
                form=request.form,
            ), 400
        users_col.insert_one({
            "name": name,
            "username": username,
            "username_key": username.lower(),
            "password_hash": generate_password_hash(password),
            "type": user_type,
            "created_at": utcnow(),
        })
        return redirect(url_for("users"))
    return render_template("user_form.html", account=None)


@app.route("/users/<user_id>/edit", methods=["GET", "POST"])
@admin_required
def user_edit(user_id):
    oid = object_id(user_id)
    user = users_col.find_one({"_id": oid}) if oid else None
    if not user:
        abort(404)
    prepared_user = serialize_doc(user)
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        try:
            user_type = int(request.form.get("type", str(user.get("type", 2))))
        except ValueError:
            user_type = int(user.get("type", 2))
        if not name or not valid_username(username):
            return render_template("user_form.html", account=prepared_user, error="Name and a valid username are required.", form=request.form), 400
        duplicate = users_col.find_one({"username_key": username.lower(), "_id": {"$ne": user["_id"]}})
        if duplicate:
            return render_template("user_form.html", account=prepared_user, error="That username is already in use.", form=request.form), 400
        if len(password) and len(password) < 8:
            return render_template("user_form.html", account=prepared_user, error="Password must be at least 8 characters.", form=request.form), 400
        update = {
            "name": name,
            "username": username,
            "username_key": username.lower(),
            "type": user_type,
        }
        if password:
            update["password_hash"] = generate_password_hash(password)
        users_col.update_one({"_id": user["_id"]}, {"$set": update})
        if current_user() and current_user().get("id") == str(user["_id"]):
            session["user"].update({"name": name, "username": username, "type": user_type})
        return redirect(url_for("users"))
    return render_template("user_form.html", account=prepared_user)


@app.route("/users/<user_id>/delete", methods=["POST"])
@admin_required
def user_delete(user_id):
    oid = object_id(user_id)
    user = users_col.find_one({"_id": oid}) if oid else None
    if not user:
        abort(404)
    if current_user() and current_user().get("id") == str(user["_id"]):
        abort(400, description="You cannot delete your own account")
    uid = user["_id"]
    comment_ids = [comment["_id"] for comment in comments_col.find({"user_id": uid}, {"_id": 1})]
    replies_col.delete_many({"user_id": uid})
    replies_col.delete_many({"comment_id": {"$in": comment_ids}})
    comments_col.delete_many({"user_id": uid})
    forum_views_col.delete_many({"user_id": uid})
    users_col.delete_one({"_id": uid})
    return redirect(url_for("users"))


@app.route("/search")
@login_required
def search():
    keyword = (request.args.get("keyword") or "").strip()
    results = []
    if keyword:
        regex = {"$regex": re.escape(keyword), "$options": "i"}
        matching_categories = list(categories_col.find({"name": regex}, {"_id": 1}))
        category_ids = [category["_id"] for category in matching_categories]
        query = {
            "$or": [
                {"title": regex},
                {"content": regex},
            ]
        }
        if category_ids:
            query["$or"].append({"category_ids": {"$in": category_ids}})
        raw_topics = list(topics_col.find(query).sort("created_at", DESCENDING).limit(100))
        results = prepare_topics(raw_topics)
        for result in results:
            result["is_match"] = True
    return render_template("search.html", keyword=keyword, results=results)


def chat_history_messages(uid):
    docs = list(
        chat_messages_col.find({"user_id": uid})
        .sort("created_at", DESCENDING)
        .limit(CHAT_HISTORY_LIMIT)
    )
    docs.reverse()
    return [{"role": doc["role"], "content": doc["content"]} for doc in docs]


def gemini_reply(history):
    if not GEMINI_API_KEY:
        return None, "Chatbot is not configured."

    contents = []
    for item in history:
        role = "model" if item.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": item.get("content", "")}]})

    url = f"{GEMINI_API_BASE}/{GEMINI_MODEL}:generateContent"
    try:
        response = requests.post(
            url,
            headers={
                "x-goog-api-key": GEMINI_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "systemInstruction": {"parts": [{"text": CHAT_SYSTEM_PROMPT}]},
                "contents": contents,
                "generationConfig": {"temperature": 0.7, "maxOutputTokens": 900},
            },
            timeout=30,
        )
        if response.status_code != 200:
            app.logger.error("Gemini chat failed: %s %s", response.status_code, response.text[:500])
            try:
                error_data = response.json().get("error", {})
            except ValueError:
                error_data = {}
            return None, error_data.get("message") or f"Gemini request failed with status {response.status_code}."
        payload = response.json()
        parts = payload["candidates"][0]["content"].get("parts", [])
        reply = "".join(part.get("text", "") for part in parts).strip()
        return reply or "I could not generate a response.", None
    except requests.RequestException:
        app.logger.exception("Gemini chat request failed")
        return None, "The assistant is temporarily unavailable. Please try again."
    except (KeyError, IndexError, TypeError, ValueError):
        app.logger.exception("Unexpected Gemini response format")
        return None, "The assistant returned an unexpected response. Please try again."


def fallback_reply(message):
    text = message.lower()
    if re.search(r"\b(hi|hello|hey|good morning|good afternoon)\b", text):
        return "Hello! I'm Tharik Assistant. Ask me how to use the forum, create topics, comment, search, or manage categories."
    if "create" in text and "topic" in text:
        return "To create a topic, open Discussions, click 'New topic', enter a title, choose categories, write your content, and save. Your topic will appear at the top of the discussion list."
    if "comment" in text or "reply" in text:
        return "Open any topic, scroll to the comments section, write your comment or reply in the box, and click Post comment or Reply. You can edit or delete your own comments and replies."
    if "search" in text:
        return "Use the search bar at the top of the page. It searches topic titles, content, and category names, then shows matching discussions."
    if "categor" in text or "tag" in text:
        return "Admins can manage categories from the Categories page. Categories act like tags and can be attached to topics."
    if "admin" in text or "user" in text or "permission" in text:
        return "Admins can manage users and categories. Staff and members can create topics, comments, and replies, but only owners or admins can edit or delete content."
    if "login" in text or "password" in text or "account" in text:
        return "Log in with your username and password. If you are an admin, you can create users from the Users page. Passwords are stored securely as hashes."
    if "thank" in text:
        return "You're welcome! Let me know if you need help with anything else in the forum."
    return (
        "I'm currently in fallback mode because the AI service is unavailable. "
        "I can still help with forum questions like creating topics, commenting, searching, categories, users, or login."
    )


@app.route("/api/chat/history", methods=["GET"])
@login_required
def chat_history():
    docs = list(
        chat_messages_col.find({"user_id": user_id()})
        .sort("created_at", ASCENDING)
        .limit(50)
    )
    return jsonify({
        "messages": [
            {
                "role": doc["role"],
                "content": doc["content"],
                "created_at": format_dt(doc.get("created_at")),
            }
            for doc in docs
        ]
    })


@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or request.form.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Message cannot be empty."}), 400
    if len(message) > 2000:
        return jsonify({"error": "Message is too long."}), 400

    uid = user_id()
    one_minute_ago = utcnow() - timedelta(minutes=1)
    recent_count = chat_messages_col.count_documents({
        "user_id": uid,
        "role": "user",
        "created_at": {"$gte": one_minute_ago},
    })
    if recent_count >= CHAT_RATE_LIMIT:
        return jsonify({"error": "Too many messages. Please wait a minute and try again."}), 429

    chat_messages_col.insert_one({
        "user_id": uid,
        "role": "user",
        "content": message,
        "created_at": utcnow(),
    })

    history = chat_history_messages(uid)
    reply, error = gemini_reply(history)
    if error:
        reply = fallback_reply(message)

    chat_messages_col.insert_one({
        "user_id": uid,
        "role": "assistant",
        "content": reply,
        "created_at": utcnow(),
    })
    return jsonify({"reply": reply})


@app.route("/api/chat/clear", methods=["POST"])
@login_required
def chat_clear():
    chat_messages_col.delete_many({"user_id": user_id()})
    return jsonify({"ok": True})


@app.route("/health")
def health():
    return {"status": "ok", "database": MONGO_DB}


if __name__ == "__main__":
    try:
        ensure_db()
    except ServerSelectionTimeoutError:
        raise SystemExit(
            "MongoDB Atlas is not reachable. Check the MONGO_URI connection string in app.py."
        )
    app.run(host="0.0.0.0", port=5000, debug=False)
