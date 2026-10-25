
from pathlib import Path
from functools import wraps
import os
import uuid

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, abort, send_from_directory
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get(
    "TRUSTVAULT_SECRET", "dev-only-change-this-secret"
)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///trustvault.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

db = SQLAlchemy(app)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "private_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "docx", "txt"}


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="owner")


class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    filename = db.Column(db.String(255), unique=True, nullable=False)
    category = db.Column(db.String(100), nullable=False)
    owner_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False
    )
    uploaded_at = db.Column(
        db.DateTime, server_default=db.func.current_timestamp()
    )


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.")
            return redirect(url_for("home"))

        user = db.session.get(User, session["user_id"])

        if user is None:
            session.clear()
            return redirect(url_for("home"))

        return func(*args, **kwargs)

    return wrapper


@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not username or not email or len(password) < 8:
            flash("Enter your details and a password of at least 8 characters.")
            return redirect(url_for("register"))

        if User.query.filter_by(email=email).first():
            flash("This email is already registered.")
            return redirect(url_for("register"))

        user = User(
            username=username,
            email=email,
            password=generate_password_hash(password),
            role="owner"
        )

        db.session.add(user)

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("This email is already registered.")
            return redirect(url_for("register"))

        flash("Account created! Please log in.")
        return redirect(url_for("home"))

    return render_template("register.html")


# Only ONE login route

@app.route("/login", methods=["POST"])
def login():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    user = User.query.filter_by(email=email).first()

    if user and check_password_hash(user.password, password):
        session.clear()
        session["user_id"] = user.id
        session["username"] = user.username
        return redirect(url_for("dashboard"))

    flash("Invalid email or password.")
    return redirect(url_for("home"))



@app.route("/dashboard")
@login_required
def dashboard():
    user = db.session.get(User, session["user_id"])

    documents = Document.query.filter_by(
        owner_id=user.id
    ).order_by(Document.id.desc()).all()

    return render_template(
        "dashboard.html",
        user=user,
        documents=documents
    )


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload_document():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        file = request.files.get("document")

        if not title or not category or not file or not file.filename:
            flash("Please complete all fields.")
            return redirect(url_for("upload_document"))

        original_name = secure_filename(file.filename)

        if "." not in original_name:
            flash("Invalid file type.")
            return redirect(url_for("upload_document"))

        extension = original_name.rsplit(".", 1)[1].lower()

        if extension not in ALLOWED_EXTENSIONS:
            flash("File type not allowed.")
            return redirect(url_for("upload_document"))

        stored_name = f"{uuid.uuid4().hex}.{extension}"
        file_path = UPLOAD_DIR / stored_name

        try:
            file.save(file_path)

            document = Document(
                title=title,
                filename=stored_name,
                category=category,
                owner_id=session["user_id"]
            )

            db.session.add(document)
            db.session.commit()

        except Exception:
            db.session.rollback()
            file_path.unlink(missing_ok=True)
            app.logger.exception("Document upload failed")
            flash("Upload failed. Please try again.")
            return redirect(url_for("upload_document"))

        flash("Document uploaded successfully!")
        return redirect(url_for("my_documents"))

    return render_template("upload.html")


@app.route("/documents")
@login_required
def my_documents():
    documents = Document.query.filter_by(
        owner_id=session["user_id"]
    ).order_by(Document.id.desc()).all()

    return render_template("documents.html", documents=documents)


@app.route("/download/<int:document_id>")
@login_required
def download_document(document_id):
    document = db.session.get(Document, document_id)

    if document is None:
        abort(404)

    if document.owner_id != session["user_id"]:
        abort(403)

    return send_from_directory(
        str(UPLOAD_DIR),
        document.filename,
        as_attachment=True,
        download_name=document.title
    )


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("home"))


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(debug=True)
