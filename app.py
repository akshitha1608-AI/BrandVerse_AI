from __future__ import annotations

import os
import re
import secrets
import hmac
import math
import hashlib
import uuid
from datetime import date, datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_jwt_extended import create_access_token, get_jwt, get_jwt_identity, jwt_required
from sqlalchemy.exc import IntegrityError
from markupsafe import Markup
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
from backend.extensions import db, jwt
from backend.matching.recommendation import rank_creators
from backend.models import (
    Application,
    BrandProfile,
    Campaign,
    CreatorProfile,
    Project,
    ProjectAnalyticsEvent,
    ProjectBookmark,
    ProjectLike,
    ProjectMedia,
    SavedCampaign,
    SavedCreator,
    RevokedToken,
    User,
)
from backend.utils.portfolio_parser import extract_portfolio_text, suggest_profile_fields

ROLES = {"creator", "brand", "agency"}
APPLICATION_STATUSES = {"Pending", "Shortlisted", "Accepted", "Rejected", "Withdrawn"}
PROJECT_TYPES = {
    "photography": "Photography",
    "video": "Video",
    "creative-idea": "Creative Idea",
    "brand-campaign": "Brand Campaign",
    "digital-artwork": "Digital Artwork",
    "mixed-media": "Mixed Media",
}
PROJECT_VISIBILITIES = {"public", "private", "unlisted"}
PROJECT_PROGRESS = {"idea-stage", "in-progress", "completed", "open-for-collaboration"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".webm"}


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    production = os.environ.get("APP_ENV", "").lower() == "production"
    secret_key = os.environ.get("SECRET_KEY")
    jwt_secret_key = os.environ.get("JWT_SECRET_KEY")
    if production and (not secret_key or not jwt_secret_key):
        raise RuntimeError("Production requires SECRET_KEY and JWT_SECRET_KEY environment variables.")
    app.config.update(
        SECRET_KEY=secret_key or "local-dev-secret-change-me",
        JWT_SECRET_KEY=jwt_secret_key or "local-dev-jwt-secret-change-me",
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'marketplace.db'}"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JWT_ACCESS_TOKEN_EXPIRES=3600,
        MAX_CONTENT_LENGTH=int(os.environ.get("MAX_CONTENT_LENGTH", 100 * 1024 * 1024)),
        MAX_PROJECT_IMAGE_BYTES=12 * 1024 * 1024,
        MAX_PROJECT_VIDEO_BYTES=80 * 1024 * 1024,
        MAX_PROJECT_FILES=12,
        UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER", str(BASE_DIR / "uploads")),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", str(production)).lower() == "true",
    )
    if test_config:
        app.config.update(test_config)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    db.init_app(app)
    jwt.init_app(app)
    CORS(app, resources={r"/api/*": {"origins": os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173")}})
    limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri="memory://")

    @jwt.unauthorized_loader
    def missing_token(_reason):
        return jsonify(error="Authentication required"), 401

    @jwt.invalid_token_loader
    def invalid_token(_reason):
        return jsonify(error="Invalid authentication token"), 401

    @jwt.expired_token_loader
    def expired_token(_header, _payload):
        return jsonify(error="Authentication token expired"), 401

    @jwt.token_in_blocklist_loader
    def is_token_revoked(_header, payload):
        return RevokedToken.query.filter_by(jti=payload["jti"]).first() is not None

    with app.app_context():
        db.create_all()
        if app.config.get("SEED_DEMO", False):
            seed_demo_data()

    @app.context_processor
    def inject_csrf_helper():
        def csrf_input():
            token = session.setdefault("_csrf_token", secrets.token_urlsafe(32))
            return Markup(f'<input type="hidden" name="csrf_token" value="{token}">')
        return {"csrf_input": csrf_input, "PROJECT_TYPES": PROJECT_TYPES}

    @app.before_request
    def protect_web_forms():
        if request.method == "POST" and not request.path.startswith("/api/"):
            expected = session.get("_csrf_token", "")
            supplied = request.form.get("csrf_token", "")
            if not expected or not supplied or not hmac.compare_digest(expected, supplied):
                abort(400, description="Invalid or missing form security token.")

    def api_role_required(*roles):
        def decorator(view):
            @wraps(view)
            @jwt_required()
            def wrapped(*args, **kwargs):
                if get_jwt().get("role") not in roles:
                    return jsonify(error="You do not have permission to perform this action"), 403
                return view(*args, **kwargs)
            return wrapped
        return decorator

    def web_user(required=True):
        user_id = session.get("user_id")
        user = db.session.get(User, user_id) if user_id else None
        if not user and required:
            return None
        return user

    def web_login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = web_user()
            if not user:
                flash("Please log in to continue.", "error")
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)
        return wrapped

    def creator_login_required(view):
        @wraps(view)
        @web_login_required
        def wrapped(*args, **kwargs):
            if web_user().role != "creator":
                abort(403)
            return view(*args, **kwargs)
        return wrapped

    def serialize_creator(profile):
        user = profile.user
        return {
            "id": user.id,
            "name": user.name,
            "username": profile.username,
            "bio": profile.bio,
            "location": profile.location,
            "category": profile.category,
            "categories": profile.categories or [],
            "skills": profile.skills or [],
            "formats": profile.formats or [],
            "audience": profile.audience,
            "followers": profile.followers,
            "engagement_rate": profile.engagement_rate,
            "minimum_budget": profile.minimum_budget,
            "maximum_budget": profile.maximum_budget,
            "available": profile.available,
        }

    def serialize_campaign(campaign):
        return {
            "id": campaign.id,
            "title": campaign.title,
            "description": campaign.description,
            "brand": campaign.owner.brand_profile.company_name if campaign.owner.brand_profile else campaign.owner.name,
            "owner_id": campaign.owner_id,
            "category": campaign.category,
            "objective": campaign.objective,
            "target_audience": campaign.target_audience,
            "content_format": campaign.content_format,
            "minimum_budget": campaign.minimum_budget,
            "maximum_budget": campaign.maximum_budget,
            "creators_required": campaign.creators_required,
            "status": campaign.status,
            "allow_negotiation": campaign.allow_negotiation,
        }

    @app.template_filter("currency")
    def currency(value):
        try:
            return f"₹{float(value):,.0f}"
        except (TypeError, ValueError):
            return "Budget not specified"

    def creator_match_dict(profile):
        data = serialize_creator(profile)
        data["objectives"] = profile.skills or []
        return data

    def required_fields(data, names):
        missing = [name for name in names if not isinstance(data.get(name), str) or not data[name].strip()]
        if missing:
            return f"Missing required fields: {', '.join(missing)}"
        return None

    def json_object():
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else None

    def project_storage_path(storage_name):
        base = (Path(app.config["UPLOAD_FOLDER"]) / "portfolio").resolve()
        target = (base / storage_name).resolve()
        if base not in target.parents:
            abort(404)
        return target

    def project_file_url(media):
        return url_for("project_media_file", media_id=media.id)

    def save_project_upload(file_storage, user_id, project_id, position):
        if not file_storage or not file_storage.filename:
            return None
        original_name = Path(file_storage.filename.replace("\\", "/")).name
        extension = Path(original_name).suffix.lower()
        if extension not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
            raise ValueError("Use JPG, JPEG, PNG, WebP, MP4, or WebM files.")

        stream = file_storage.stream
        stream.seek(0, os.SEEK_END)
        file_size = stream.tell()
        stream.seek(0)
        media_type = "video" if extension in VIDEO_EXTENSIONS else "image"
        max_size = app.config["MAX_PROJECT_VIDEO_BYTES" if media_type == "video" else "MAX_PROJECT_IMAGE_BYTES"]
        if file_size <= 0 or file_size > max_size:
            limit_mb = max_size // (1024 * 1024)
            raise ValueError(f"Each {media_type} file must be no larger than {limit_mb} MB.")
        signature = stream.read(16)
        stream.seek(0)
        valid_signature = (
            (extension in {".jpg", ".jpeg"} and signature.startswith(b"\xff\xd8\xff"))
            or (extension == ".png" and signature.startswith(b"\x89PNG\r\n\x1a\n"))
            or (extension == ".webp" and signature.startswith(b"RIFF") and signature[8:12] == b"WEBP")
            or (extension == ".mp4" and b"ftyp" in signature[4:12])
            or (extension == ".webm" and signature.startswith(b"\x1aE\xdf\xa3"))
        )
        if not valid_signature:
            raise ValueError(f"{original_name} does not contain valid {extension[1:].upper()} media.")

        storage_name = f"{user_id}/{uuid.uuid4().hex}{extension}"
        destination = project_storage_path(storage_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        saved = False
        try:
            file_storage.save(destination)
            saved = destination.stat().st_size == file_size
            if not saved:
                raise OSError("Saved media size did not match the validated upload size.")
            return ProjectMedia(
                project_id=project_id,
                storage_name=storage_name,
                original_name=original_name[:255],
                media_type=media_type,
                mime_type={
                    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
                    ".mp4": "video/mp4", ".webm": "video/webm",
                }[extension],
                file_size=file_size,
                display_order=position,
            ), destination
        finally:
            if not saved:
                destination.unlink(missing_ok=True)

    def project_owner_or_404(project_id, user):
        project = db.session.get(Project, project_id) or abort(404)
        if project.user_id != user.id:
            abort(404)
        return project

    def public_project_query():
        return Project.query.filter_by(status="published", visibility="public")

    def project_return_target(default_endpoint="portfolio"):
        target = request.form.get("return_to", "")
        parsed_target = urlsplit(target)
        if target.startswith("/") and not target.startswith("//") and "\\" not in target and not parsed_target.scheme and not parsed_target.netloc:
            return target
        return url_for(default_endpoint)

    @app.get("/")
    def home():
        featured = Campaign.query.filter_by(status="published").order_by(Campaign.created_at.desc()).limit(4).all()
        return render_template(
            "index.html",
            campaigns=featured,
            creator_count=CreatorProfile.query.count(),
            campaign_count=Campaign.query.filter_by(status="published").count(),
            user=web_user(False),
        )

    @app.route("/register", methods=["GET", "POST"])
    @limiter.limit("5 per minute", methods=["POST"])
    def register():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            role = request.form.get("role", "")
            if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(password) < 8 or role not in ROLES:
                flash("Enter a valid name and email, choose a role, and use a password of at least 8 characters.", "error")
                return render_template("auth.html", mode="register")
            if User.query.filter_by(email=email).first():
                flash("An account with that email already exists.", "error")
                return render_template("auth.html", mode="register")
            user = User(name=name, email=email, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            if role == "creator":
                username = re.sub(r"[^a-z0-9_]", "", email.split("@")[0])[:75] or f"creator{user.id}"
                while CreatorProfile.query.filter_by(username=username).first():
                    username = f"{username[:70]}_{user.id}"
                db.session.add(CreatorProfile(user_id=user.id, username=username))
            else:
                db.session.add(BrandProfile(user_id=user.id, company_name=name))
            db.session.commit()
            session.clear()
            session["user_id"] = user.id
            flash("Account created. Complete your profile to improve your matches.", "success")
            return redirect(url_for("dashboard"))
        return render_template("auth.html", mode="register")

    @app.route("/login", methods=["GET", "POST"])
    @limiter.limit("5 per minute", methods=["POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = User.query.filter_by(email=email).first()
            if not user or not user.check_password(password):
                flash("Email or password is incorrect.", "error")
                return render_template("auth.html", mode="login")
            session.clear()
            session["user_id"] = user.id
            flash(f"Welcome back, {user.name}.", "success")
            next_target = request.args.get("next", "")
            parsed_target = urlsplit(next_target)
            if (
                next_target.startswith("/")
                and not next_target.startswith("//")
                and "\\" not in next_target
                and not parsed_target.scheme
                and not parsed_target.netloc
            ):
                return redirect(next_target)
            return redirect(url_for("dashboard"))
        return render_template("auth.html", mode="login")

    @app.post("/logout")
    def logout():
        session.clear()
        flash("You have been logged out.", "success")
        return redirect(url_for("home"))

    @app.get("/dashboard")
    @web_login_required
    def dashboard():
        user = web_user()
        campaigns = Campaign.query.filter_by(status="published").all()
        recommendations = []
        applications = []
        if user.role == "creator" and user.creator_profile:
            profile = user.creator_profile
            recommendations = rank_campaigns_for_creator(profile, campaigns)
            applications = Application.query.filter_by(creator_id=user.id).order_by(Application.created_at.desc()).limit(5).all()
        elif user.role in {"brand", "agency"}:
            campaigns = Campaign.query.filter_by(owner_id=user.id).order_by(Campaign.created_at.desc()).all()
            applications = Application.query.join(Campaign).filter(Campaign.owner_id == user.id).order_by(Application.created_at.desc()).limit(8).all()
        return render_template("dashboard.html", user=user, campaigns=campaigns, recommendations=recommendations, applications=applications)

    def rank_campaigns_for_creator(profile, campaigns):
        creator = creator_match_dict(profile)
        matched = []
        for campaign in campaigns:
            match = rank_creators([creator], serialize_campaign(campaign))
            if match:
                matched.append({**serialize_campaign(campaign), "match": match[0]["match"]})
        return sorted(matched, key=lambda row: row["match"]["score"], reverse=True)[:5]

    @app.get("/campaigns")
    def browse_campaigns():
        query = Campaign.query.filter_by(status="published")
        category = request.args.get("category", "").strip()
        search = request.args.get("q", "").strip()
        if category:
            query = query.filter(Campaign.category.ilike(f"%{category}%"))
        if search:
            query = query.filter(db.or_(Campaign.title.ilike(f"%{search}%"), Campaign.description.ilike(f"%{search}%")))
        campaigns = query.order_by(Campaign.created_at.desc()).all()
        return render_template("campaigns.html", campaigns=campaigns, user=web_user(False), search=search, category=category)

    @app.get("/creators")
    def browse_creators():
        query = CreatorProfile.query.join(User).filter(CreatorProfile.available.is_(True))
        search = request.args.get("q", "").strip()
        category = request.args.get("category", "").strip()
        if search:
            query = query.filter(db.or_(User.name.ilike(f"%{search}%"), CreatorProfile.username.ilike(f"%{search}%")))
        if category:
            query = query.filter(CreatorProfile.category.ilike(f"%{category}%"))
        order_by = User.created_at.desc() if request.args.get("sort") == "newest" else User.name
        creators = query.order_by(order_by).limit(100).all()
        return render_template("creators.html", creators=creators, user=web_user(False), search=search, category=category)

    @app.get("/creators/<int:creator_id>")
    def creator_detail(creator_id):
        creator = db.session.get(User, creator_id)
        if not creator or creator.role != "creator" or not creator.creator_profile:
            abort(404)
        projects = public_project_query().filter_by(user_id=creator.id).order_by(Project.created_at.desc()).limit(12).all()
        return render_template("creator_detail.html", creator=creator, profile=creator.creator_profile, projects=projects, user=web_user(False))

    @app.get("/creator/<string:username>")
    def creator_portfolio(username):
        profile = CreatorProfile.query.filter_by(username=username).first_or_404()
        creator = profile.user
        projects = public_project_query().filter_by(user_id=creator.id).order_by(Project.created_at.desc()).all()
        return render_template("creator_detail.html", creator=creator, profile=profile, projects=projects, user=web_user(False))

    @app.post("/creators/<int:creator_id>/save")
    @web_login_required
    def save_creator(creator_id):
        user = web_user()
        if user.role not in {"brand", "agency"}:
            abort(403)
        if not db.session.get(User, creator_id) or db.session.get(User, creator_id).role != "creator":
            abort(404)
        db.session.add(SavedCreator(brand_id=user.id, creator_id=creator_id))
        try:
            db.session.commit()
            flash("Creator saved to your shortlist.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Creator is already saved.", "error")
        return redirect(url_for("creator_detail", creator_id=creator_id))

    @app.post("/campaigns/<int:campaign_id>/save")
    @web_login_required
    def save_campaign(campaign_id):
        user = web_user()
        campaign = db.session.get(Campaign, campaign_id)
        if user.role != "creator" or not campaign or campaign.status != "published":
            abort(403)
        db.session.add(SavedCampaign(creator_id=user.id, campaign_id=campaign_id))
        try:
            db.session.commit()
            flash("Campaign saved.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Campaign is already saved.", "error")
        return redirect(url_for("campaign_detail", campaign_id=campaign_id))

    @app.get("/campaigns/<int:campaign_id>/creator-matches")
    @web_login_required
    def web_creator_matches(campaign_id):
        user = web_user()
        campaign = db.session.get(Campaign, campaign_id) or abort(404)
        if user.id != campaign.owner_id or user.role not in {"brand", "agency"}:
            abort(403)
        candidates = rank_creators(
            [creator_match_dict(profile) for profile in CreatorProfile.query.filter_by(available=True).all()],
            serialize_campaign(campaign),
        )
        return render_template("creator_matches.html", campaign=campaign, candidates=candidates, user=user)

    @app.route("/campaigns/new", methods=["GET", "POST"])
    @web_login_required
    def new_campaign():
        user = web_user()
        if user.role not in {"brand", "agency"}:
            abort(403)
        if request.method == "POST":
            error = required_fields(request.form, ["title", "description", "category", "target_audience", "content_format", "minimum_budget", "maximum_budget"])
            try:
                minimum = float(request.form.get("minimum_budget", 0))
                maximum = float(request.form.get("maximum_budget", 0))
                creators_required = int(request.form.get("creators_required", 1))
            except (ValueError, OverflowError):
                minimum = maximum = 0
                creators_required = 0
            if error or not math.isfinite(minimum) or not math.isfinite(maximum) or minimum < 0 or maximum < minimum or not 1 <= creators_required <= 100:
                flash(error or "Enter a valid budget range and creator count (1–100).", "error")
                return render_template("campaign_form.html", user=user)
            campaign = Campaign(
                owner_id=user.id,
                title=request.form["title"].strip(),
                description=request.form["description"].strip(),
                category=request.form["category"].strip(),
                objective=request.form.get("objective", "").strip(),
                target_audience=request.form["target_audience"].strip(),
                content_format=request.form["content_format"].strip(),
                minimum_budget=minimum,
                maximum_budget=maximum,
                creators_required=creators_required,
                status="published" if request.form.get("publish") else "draft",
                allow_negotiation=request.form.get("allow_negotiation") == "on",
            )
            db.session.add(campaign)
            db.session.commit()
            flash("Campaign published." if campaign.status == "published" else "Campaign saved as draft.", "success")
            return redirect(url_for("dashboard"))
        return render_template("campaign_form.html", user=user)

    @app.route("/campaigns/<int:campaign_id>/edit", methods=["GET", "POST"])
    @web_login_required
    def edit_campaign(campaign_id):
        user = web_user()
        campaign = db.session.get(Campaign, campaign_id) or abort(404)
        if user.id != campaign.owner_id or user.role not in {"brand", "agency"}:
            abort(403)
        if request.method == "POST":
            error = required_fields(request.form, ["title", "description", "category", "target_audience", "content_format", "minimum_budget", "maximum_budget"])
            try:
                minimum = float(request.form.get("minimum_budget", 0))
                maximum = float(request.form.get("maximum_budget", 0))
                creators_required = int(request.form.get("creators_required", 1))
            except (ValueError, OverflowError):
                minimum = maximum = 0
                creators_required = 0
            if error or not math.isfinite(minimum) or not math.isfinite(maximum) or minimum < 0 or maximum < minimum or not 1 <= creators_required <= 100:
                flash(error or "Enter a valid budget range and creator count (1–100).", "error")
                return render_template("campaign_form.html", user=user, campaign=campaign)
            campaign.title = request.form["title"].strip()
            campaign.description = request.form["description"].strip()
            campaign.category = request.form["category"].strip()
            campaign.objective = request.form.get("objective", "").strip()
            campaign.target_audience = request.form["target_audience"].strip()
            campaign.content_format = request.form["content_format"].strip()
            campaign.minimum_budget = minimum
            campaign.maximum_budget = maximum
            campaign.creators_required = creators_required
            campaign.allow_negotiation = request.form.get("allow_negotiation") == "on"
            campaign.status = "published" if request.form.get("publish") else "draft"
            db.session.commit()
            flash("Campaign updated.", "success")
            return redirect(url_for("campaign_detail", campaign_id=campaign.id))
        return render_template("campaign_form.html", user=user, campaign=campaign)

    @app.get("/campaigns/<int:campaign_id>")
    def campaign_detail(campaign_id):
        campaign = db.session.get(Campaign, campaign_id) or abort(404)
        user = web_user(False)
        if campaign.status != "published" and (not user or user.id != campaign.owner_id):
            abort(404)
        existing = Application.query.filter_by(campaign_id=campaign.id, creator_id=user.id).first() if user and user.role == "creator" else None
        return render_template("campaign_detail.html", campaign=campaign, user=user, existing=existing)

    @app.post("/campaigns/<int:campaign_id>/apply")
    @web_login_required
    def apply_campaign(campaign_id):
        user = web_user()
        campaign = db.session.get(Campaign, campaign_id) or abort(404)
        if user.role != "creator" or campaign.status != "published":
            abort(403)
        application = Application(campaign_id=campaign.id, creator_id=user.id, message=request.form.get("message", "").strip())
        db.session.add(application)
        try:
            db.session.commit()
            flash("Your application was sent.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("You have already applied to this campaign.", "error")
        return redirect(url_for("campaign_detail", campaign_id=campaign.id))

    @app.post("/applications/<int:application_id>/status")
    @web_login_required
    def web_application_status(application_id):
        user = web_user()
        application = db.session.get(Application, application_id) or abort(404)
        if user.id != application.campaign.owner_id or user.role not in {"brand", "agency"}:
            abort(403)
        status = request.form.get("status")
        if status not in APPLICATION_STATUSES - {"Withdrawn"}:
            abort(400)
        application.status = status
        db.session.commit()
        flash(f"Application marked {status.lower()}.", "success")
        return redirect(url_for("dashboard"))

    @app.get("/profile")
    @web_login_required
    def profile():
        user = web_user()
        portfolio_suggestions = session.pop("portfolio_suggestions", None)
        return render_template("profile.html", user=user, portfolio_suggestions=portfolio_suggestions)

    @app.post("/profile")
    @web_login_required
    def update_profile():
        user = web_user()
        if user.role == "creator" and user.creator_profile:
            profile = user.creator_profile
            minimum_budget = float_or_none(request.form.get("minimum_budget"))
            maximum_budget = float_or_none(request.form.get("maximum_budget"))
            followers = integer_or_none(request.form.get("followers"))
            engagement = float_or_none(request.form.get("engagement_rate"))
            if any(value is not None and value < 0 for value in (minimum_budget, maximum_budget, followers, engagement)) or (
                minimum_budget is not None and maximum_budget is not None and maximum_budget < minimum_budget
            ) or (engagement is not None and engagement > 100):
                flash("Enter non-negative profile figures and ensure the maximum rate is at least the minimum (engagement up to 100%).", "error")
                return redirect(url_for("profile"))
            profile.bio = request.form.get("bio", "").strip()
            profile.category = request.form.get("category", "").strip()
            profile.categories = split_values(request.form.get("categories", ""))
            profile.skills = split_values(request.form.get("skills", ""))
            profile.formats = split_values(request.form.get("formats", ""))
            profile.audience = request.form.get("audience", "").strip()
            profile.location = request.form.get("location", "").strip()
            profile.minimum_budget = minimum_budget
            profile.maximum_budget = maximum_budget
            profile.followers = followers
            profile.engagement_rate = engagement
            profile.available = request.form.get("available") == "on"
        elif user.brand_profile:
            profile = user.brand_profile
            profile.company_name = request.form.get("company_name", "").strip() or user.name
            profile.industry = request.form.get("industry", "").strip()
            profile.description = request.form.get("description", "").strip()
            profile.location = request.form.get("location", "").strip()
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))

    def float_or_none(value):
        try:
            result = float(value) if value else None
            return result if result is None or math.isfinite(result) else -1
        except ValueError:
            return -1 if value else None

    def integer_or_none(value):
        try:
            return int(value) if value else None
        except ValueError:
            return -1

    def split_values(value):
        return [item.strip() for item in value.split(",") if item.strip()][:30]

    @app.post("/portfolio/analyze")
    @web_login_required
    def portfolio_analyze():
        user = web_user()
        if not user.creator_profile:
            abort(403)
        try:
            text = extract_portfolio_text(request.files.get("portfolio"), min(app.config["MAX_CONTENT_LENGTH"], 5 * 1024 * 1024))
            user.creator_profile.portfolio_text = text
            db.session.commit()
            session["portfolio_suggestions"] = suggest_profile_fields(text)
            flash(f"Extracted {len(text)} characters from the portfolio. Review and update profile fields before saving.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("profile"))

    def populate_project(project, form):
        title = form.get("title", "").strip()
        project_type = form.get("project_type", "").strip()
        visibility = form.get("visibility", "private").strip()
        progress = form.get("progress", "completed").strip()
        if not title or len(title) > 180 or project_type not in PROJECT_TYPES:
            raise ValueError("Add a project title (up to 180 characters) and choose a valid project type.")
        if visibility not in PROJECT_VISIBILITIES or progress not in PROJECT_PROGRESS:
            raise ValueError("Choose a valid visibility and project progress value.")
        project.title = title
        project.description = form.get("description", "").strip()[:12000]
        project.project_type = project_type
        project.category = form.get("category", "").strip()[:100]
        project.tags = split_values(form.get("tags", ""))[:20]
        project.client_name = form.get("client_name", "").strip()[:160]
        project.credits = form.get("credits", "").strip()[:3000]
        project.location = form.get("location", "").strip()[:160]
        project.tools = form.get("tools", "").strip()[:1000]
        project.creative_process = form.get("creative_process", "").strip()[:6000]
        project.target_audience = form.get("target_audience", "").strip()[:240]
        project.campaign_objective = form.get("campaign_objective", "").strip()[:4000]
        project.campaign_results = form.get("campaign_results", "").strip()[:4000]
        project.visibility = visibility
        project.progress = progress
        external_url = form.get("external_video_url", "").strip()
        if external_url:
            parsed = urlsplit(external_url)
            host = (parsed.hostname or "").lower()
            supported_hosts = ("youtube.com", "youtu.be", "vimeo.com", "instagram.com", "tiktok.com")
            if parsed.scheme != "https" or not any(host == allowed or host.endswith(f".{allowed}") for allowed in supported_hosts):
                raise ValueError("External video links must use HTTPS and come from YouTube, Vimeo, Instagram, or TikTok.")
        project.external_video_url = external_url[:500]
        raw_date = form.get("project_date", "").strip()
        try:
            project.project_date = date.fromisoformat(raw_date) if raw_date else None
        except ValueError:
            raise ValueError("Enter a valid project date.")

    def save_project_files(project, files, removed_ids):
        new_paths = []
        active_media_count = sum(1 for media in project.media if media.id not in removed_ids)
        uploads = [item for item in files if item and item.filename]
        if active_media_count + len(uploads) > app.config["MAX_PROJECT_FILES"]:
            raise ValueError(f"A project can contain up to {app.config['MAX_PROJECT_FILES']} media files.")
        total_size = 0
        staged_uploads = []
        for item in uploads:
            stream = item.stream
            stream.seek(0, os.SEEK_END)
            total_size += stream.tell()
            stream.seek(0)
        request_limit = app.config.get("MAX_CONTENT_LENGTH")
        if request_limit is not None and total_size > max(0, request_limit - 1024 * 1024):
            raise ValueError("The combined media upload is too large for a single request.")
        for item in uploads:
            media, path = save_project_upload(item, project.user_id, project.id, active_media_count + len(staged_uploads))
            staged_uploads.append(media)
            new_paths.append(path)
        for media in staged_uploads:
            project.media.append(media)
        captions = request.form.getlist("new_media_caption")
        for media, caption in zip(staged_uploads, captions):
            media.caption = caption.strip()[:240]
        for media in project.media:
            if media.id in removed_ids:
                continue
            if media.id is not None:
                media.caption = request.form.get(f"media_caption_{media.id}", media.caption).strip()[:240]
            media.is_cover = False
        cover_value = request.form.get("cover_media_id", "").strip()
        cover_media = next((media for media in project.media if cover_value and str(media.id) == cover_value and media.id not in removed_ids), None)
        if cover_value and not cover_media:
            raise ValueError("Choose a cover image that belongs to this project.")
        if cover_media:
            cover_media.is_cover = True
        elif not any(media.is_cover and media.id not in removed_ids for media in project.media):
            first_cover = next((media for media in project.media if media.id not in removed_ids and media.media_type == "image"), None)
            if not first_cover:
                first_cover = next((media for media in project.media if media.id not in removed_ids), None)
            if first_cover:
                first_cover.is_cover = True
        return new_paths

    def cleanup_new_uploads(paths):
        for path in paths:
            path.unlink(missing_ok=True)

    def record_project_event(project, event_type):
        visitor = session.setdefault("_portfolio_visitor", secrets.token_urlsafe(32))
        event_key = hmac.new(
            app.config["SECRET_KEY"].encode(),
            f"{project.id}:{event_type}:{visitor}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if ProjectAnalyticsEvent.query.filter_by(project_id=project.id, event_type=event_type, visitor_key=event_key).first():
            return
        db.session.add(ProjectAnalyticsEvent(
            project_id=project.id,
            event_type=event_type,
            visitor_key=event_key,
        ))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()

    @app.get("/showcase")
    def discover_projects():
        project_type = request.args.get("type", "").strip()
        category = request.args.get("category", "").strip()
        search = request.args.get("q", "").strip()[:120]
        sort = request.args.get("sort", "newest").strip()
        feed = request.args.get("feed", "work").strip()
        profiles = []
        if feed == "new-creators":
            profiles = CreatorProfile.query.join(User).order_by(User.created_at.desc()).limit(30).all()
        elif feed == "featured-portfolios":
            featured_ids = db.session.query(
                CreatorProfile.user_id,
                db.func.count(Project.id).label("project_count"),
            ).join(Project, Project.user_id == CreatorProfile.user_id).filter(
                Project.status == "published",
                Project.visibility == "public",
            ).group_by(CreatorProfile.user_id).order_by(
                db.func.count(Project.id).desc(),
                CreatorProfile.user_id,
            ).limit(30).all()
            profiles_by_id = {
                profile.user_id: profile
                for profile in CreatorProfile.query.filter(CreatorProfile.user_id.in_([row.user_id for row in featured_ids])).all()
            }
            profiles = [profiles_by_id[row.user_id] for row in featured_ids if row.user_id in profiles_by_id]
        if feed in {"new-creators", "featured-portfolios"}:
            return render_template(
                "showcase.html",
                projects=[],
                profiles=profiles,
                feed=feed,
                project_types=PROJECT_TYPES,
                selected_type="",
                category="",
                search="",
                sort="newest",
                user=web_user(False),
            )
        feed = "work"
        query = public_project_query().join(User)
        if project_type in PROJECT_TYPES:
            query = query.filter(Project.project_type == project_type)
        else:
            project_type = ""
        if category:
            query = query.filter(Project.category.ilike(f"%{category}%"))
        if search:
            query = query.filter(db.or_(
                Project.title.ilike(f"%{search}%"),
                Project.description.ilike(f"%{search}%"),
                Project.category.ilike(f"%{search}%"),
                Project.client_name.ilike(f"%{search}%"),
                User.name.ilike(f"%{search}%"),
                db.cast(Project.tags, db.String).ilike(f"%{search}%"),
            ))
        if sort == "popular":
            likes_count = db.session.query(
                ProjectLike.project_id.label("project_id"),
                db.func.count(ProjectLike.id).label("like_count"),
            ).group_by(ProjectLike.project_id).subquery()
            query = query.outerjoin(likes_count, Project.id == likes_count.c.project_id).order_by(db.func.coalesce(likes_count.c.like_count, 0).desc(), Project.created_at.desc())
        else:
            sort = "newest"
            query = query.order_by(Project.created_at.desc())
        projects = query.limit(60).all()
        return render_template(
            "showcase.html",
            projects=projects,
            profiles=profiles,
            feed=feed,
            project_types=PROJECT_TYPES,
            selected_type=project_type,
            category=category,
            search=search,
            sort=sort,
            user=web_user(False),
        )

    @app.get("/portfolio")
    @creator_login_required
    def portfolio():
        user = web_user()
        projects = Project.query.filter_by(user_id=user.id).order_by(Project.updated_at.desc()).all()
        saved_projects = Project.query.join(ProjectBookmark).filter(ProjectBookmark.user_id == user.id).order_by(ProjectBookmark.created_at.desc()).limit(12).all()
        project_ids = [project.id for project in projects]
        total_views = ProjectAnalyticsEvent.query.filter(
            ProjectAnalyticsEvent.project_id.in_(project_ids or [-1]),
            ProjectAnalyticsEvent.event_type == "view",
        ).count()
        total_likes = ProjectLike.query.join(Project).filter(Project.user_id == user.id).count()
        return render_template(
            "portfolio.html",
            user=user,
            projects=projects,
            saved_projects=saved_projects,
            total_views=total_views,
            total_likes=total_likes,
            published_count=sum(project.status == "published" for project in projects),
        )

    @app.get("/saved-projects")
    @web_login_required
    def saved_projects():
        user = web_user()
        projects = Project.query.join(ProjectBookmark).filter(
            ProjectBookmark.user_id == user.id,
            Project.status == "published",
            Project.visibility == "public",
        ).order_by(ProjectBookmark.created_at.desc()).all()
        return render_template("saved_projects.html", user=user, projects=projects)

    @app.get("/portfolio/analytics")
    @creator_login_required
    def portfolio_analytics():
        user = web_user()
        projects = Project.query.filter_by(user_id=user.id).order_by(Project.updated_at.desc()).all()
        metrics = []
        for project in projects:
            events = db.session.query(ProjectAnalyticsEvent.event_type, db.func.count(ProjectAnalyticsEvent.id)).filter_by(project_id=project.id).group_by(ProjectAnalyticsEvent.event_type).all()
            counts = {event_type: count for event_type, count in events}
            metrics.append({
                "project": project,
                "views": counts.get("view", 0),
                "video_starts": counts.get("video_start", 0),
                "video_completions": counts.get("video_complete", 0),
                "shares": counts.get("share", 0),
                "likes": ProjectLike.query.filter_by(project_id=project.id).count(),
                "saves": ProjectBookmark.query.filter_by(project_id=project.id).count(),
            })
        return render_template("portfolio_analytics.html", user=user, metrics=metrics)

    @app.route("/projects/new", methods=["GET", "POST"])
    @creator_login_required
    def create_project():
        user = web_user()
        project = Project(user_id=user.id, title="", project_type="photography", visibility="private", status="draft")
        initial_type = request.args.get("type", "")
        if initial_type in PROJECT_TYPES:
            project.project_type = initial_type
        if request.method == "POST":
            saved_paths = []
            try:
                populate_project(project, request.form)
                files = request.files.getlist("media")
                prepared_files = [item for item in files if item and item.filename]
                if not prepared_files and not project.external_video_url and project.project_type not in {"creative-idea"}:
                    raise ValueError("Add at least one media file, or include a supported external video link.")
                project.status = "published" if request.form.get("action") == "publish" else "draft"
                db.session.add(project)
                db.session.flush()
                saved_paths = save_project_files(project, files, set())
                db.session.commit()
                flash("Project published." if project.status == "published" else "Project saved as a draft.", "success")
                return redirect(url_for("project_detail", project_id=project.id))
            except ValueError as exc:
                db.session.rollback()
                cleanup_new_uploads(saved_paths)
                flash(str(exc), "error")
            except (OSError, IntegrityError):
                db.session.rollback()
                cleanup_new_uploads(saved_paths)
                app.logger.exception("Unable to store project media.")
                flash("The project could not be saved. Check available disk storage and try again.", "error")
        return render_template(
            "project_form.html",
            user=user,
            project=project,
            project_types=PROJECT_TYPES,
            max_files=app.config["MAX_PROJECT_FILES"],
            max_image_bytes=app.config["MAX_PROJECT_IMAGE_BYTES"],
            max_video_bytes=app.config["MAX_PROJECT_VIDEO_BYTES"],
            max_request_bytes=app.config["MAX_CONTENT_LENGTH"],
            ai_ready=False,
        )

    @app.route("/projects/<int:project_id>/edit", methods=["GET", "POST"])
    @creator_login_required
    def edit_project(project_id):
        user = web_user()
        project = project_owner_or_404(project_id, user)
        if request.method == "POST":
            saved_paths = []
            try:
                populate_project(project, request.form)
                removed_ids = {int(value) for value in request.form.getlist("remove_media") if value.isdigit()}
                if removed_ids - {media.id for media in project.media if media.id is not None}:
                    raise ValueError("Selected media does not belong to this project.")
                uploads = request.files.getlist("media")
                live_media = [media for media in project.media if media.id not in removed_ids]
                if not live_media and not any(item and item.filename for item in uploads) and not project.external_video_url and project.project_type != "creative-idea":
                    raise ValueError("Keep at least one media file, or include a supported external video link.")
                project.status = "published" if request.form.get("action") == "publish" else "draft"
                project_id_value = project.id
                saved_paths = save_project_files(project, uploads, removed_ids)
                removed_paths = [project_storage_path(media.storage_name) for media in project.media if media.id in removed_ids]
                for media in list(project.media):
                    if media.id in removed_ids:
                        project.media.remove(media)
                db.session.commit()
                for path in removed_paths:
                    path.unlink(missing_ok=True)
                flash("Project updated.", "success")
                return redirect(url_for("project_detail", project_id=project_id_value))
            except ValueError as exc:
                db.session.rollback()
                cleanup_new_uploads(saved_paths)
                flash(str(exc), "error")
            except (OSError, IntegrityError):
                db.session.rollback()
                cleanup_new_uploads(saved_paths)
                app.logger.exception("Unable to update project media.")
                flash("The project could not be updated. Check available disk storage and try again.", "error")
        return render_template(
            "project_form.html",
            user=user,
            project=project,
            project_types=PROJECT_TYPES,
            max_files=app.config["MAX_PROJECT_FILES"],
            max_image_bytes=app.config["MAX_PROJECT_IMAGE_BYTES"],
            max_video_bytes=app.config["MAX_PROJECT_VIDEO_BYTES"],
            max_request_bytes=app.config["MAX_CONTENT_LENGTH"],
            ai_ready=False,
        )

    @app.post("/projects/<int:project_id>/delete")
    @creator_login_required
    def delete_project(project_id):
        project = project_owner_or_404(project_id, web_user())
        stored_paths = [project_storage_path(media.storage_name) for media in project.media]
        db.session.delete(project)
        db.session.commit()
        for path in stored_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                app.logger.exception("Unable to remove deleted project media: %s", path.name)
        flash("Project deleted.", "success")
        return redirect(url_for("portfolio"))

    @app.get("/projects/<int:project_id>")
    def project_detail(project_id):
        project = db.session.get(Project, project_id) or abort(404)
        user = web_user(False)
        is_owner = bool(user and user.id == project.user_id)
        if not is_owner and (project.status != "published" or project.visibility == "private"):
            abort(404)
        if not is_owner:
            record_project_event(project, "view")
        liked = bool(user and ProjectLike.query.filter_by(project_id=project.id, user_id=user.id).first())
        bookmarked = bool(user and ProjectBookmark.query.filter_by(project_id=project.id, user_id=user.id).first())
        csrf_token = session.setdefault("_csrf_token", secrets.token_urlsafe(32))
        return render_template(
            "project_detail.html",
            project=project,
            user=user,
            is_owner=is_owner,
            liked=liked,
            bookmarked=bookmarked,
            csrf_token=csrf_token,
            noindex=project.visibility == "unlisted" or project.status != "published",
        )

    @app.get("/project-media/<int:media_id>")
    def project_media_file(media_id):
        media = db.session.get(ProjectMedia, media_id) or abort(404)
        project = media.project
        user = web_user(False)
        if not (user and user.id == project.user_id) and (project.status != "published" or project.visibility == "private"):
            abort(404)
        path = project_storage_path(media.storage_name)
        if not path.is_file():
            abort(404)
        response = send_file(path, mimetype=media.mime_type, as_attachment=False, download_name=media.original_name, conditional=True)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, no-store" if project.visibility == "private" else "public, max-age=3600"
        return response

    @app.post("/projects/<int:project_id>/like")
    @web_login_required
    def toggle_project_like(project_id):
        user = web_user()
        project = public_project_query().filter_by(id=project_id).first_or_404()
        if project.user_id == user.id:
            abort(403)
        like = ProjectLike.query.filter_by(project_id=project.id, user_id=user.id).first()
        if like:
            db.session.delete(like)
            flash("Project removed from liked work.", "success")
        else:
            db.session.add(ProjectLike(project_id=project.id, user_id=user.id))
            flash("Project liked.", "success")
        db.session.commit()
        return redirect(project_return_target())

    @app.post("/projects/<int:project_id>/save")
    @web_login_required
    def toggle_project_bookmark(project_id):
        user = web_user()
        project = public_project_query().filter_by(id=project_id).first_or_404()
        if project.user_id == user.id:
            abort(403)
        bookmark = ProjectBookmark.query.filter_by(project_id=project.id, user_id=user.id).first()
        if bookmark:
            db.session.delete(bookmark)
            flash("Project removed from saved work.", "success")
        else:
            db.session.add(ProjectBookmark(project_id=project.id, user_id=user.id))
            flash("Project saved.", "success")
        db.session.commit()
        return redirect(project_return_target())

    @app.post("/projects/<int:project_id>/share")
    def share_project(project_id):
        project = public_project_query().filter_by(id=project_id).first_or_404()
        user = web_user(False)
        if not user or user.id != project.user_id:
            record_project_event(project, "share")
        flash("Share link ready. Copy the project URL from your browser.", "success")
        return redirect(project_return_target())

    @app.post("/projects/<int:project_id>/analytics")
    @limiter.limit("60 per hour")
    def project_video_analytics(project_id):
        project = public_project_query().filter_by(id=project_id).first_or_404()
        event_type = request.form.get("event_type", "")
        if event_type not in {"video_start", "video_complete"} or not any(media.media_type == "video" for media in project.media):
            abort(400)
        user = web_user(False)
        if not user or user.id != project.user_id:
            record_project_event(project, event_type)
        return ("", 204)

    @app.post("/brief/generate")
    @web_login_required
    def generate_brief():
        user = web_user()
        if user.role not in {"brand", "agency"}:
            abort(403)
        data = request.form
        title = data.get("product", "New campaign").strip()[:100]
        category = data.get("industry", "Lifestyle").strip()
        format_name = data.get("content_format", "Short-form video").strip()
        audience = data.get("audience", "Interested audiences").strip()
        draft = {
            "title": f"{category} campaign: {title}",
            "summary": f"Introduce {title} to {audience} through creator-led {format_name.lower()}.",
            "objective": "Awareness and engagement",
            "suggested_format": format_name,
            "deliverables": "1 short-form video and 2 story frames per creator",
            "timeline": "2 weeks: briefing, production, review, and publication",
            "kpis": "Reach, video views, saves, and qualified clicks",
            "budget": data.get("budget", ""),
        }
        return render_template("brief.html", user=user, draft=draft, category=category, audience=audience)

    @app.post("/brief/save")
    @web_login_required
    def save_brief():
        user = web_user()
        if user.role not in {"brand", "agency"}:
            abort(403)
        try:
            minimum = float(request.form.get("minimum_budget", 0))
            maximum = float(request.form.get("maximum_budget", 0))
        except (ValueError, OverflowError):
            minimum = maximum = 0
        if not request.form.get("title", "").strip() or not math.isfinite(minimum) or not math.isfinite(maximum) or maximum < minimum or minimum < 0:
            flash("Add a title and valid budget range before saving this brief.", "error")
            return redirect(url_for("new_campaign"))
        campaign = Campaign(
            owner_id=user.id,
            title=request.form["title"].strip(),
            description=request.form.get("description", "").strip() or request.form.get("summary", ""),
            category=request.form.get("category", "Lifestyle"),
            objective=request.form.get("objective", "Awareness"),
            target_audience=request.form.get("target_audience", ""),
            content_format=request.form.get("content_format", ""),
            minimum_budget=minimum,
            maximum_budget=maximum,
            status="draft",
        )
        db.session.add(campaign)
        db.session.commit()
        flash("Generated brief saved as an editable draft.", "success")
        return redirect(url_for("dashboard"))

    @app.get("/api/overview")
    def api_overview():
        return jsonify({
            "demo_mode": True,
            "creators": CreatorProfile.query.count(),
            "campaigns": Campaign.query.filter_by(status="published").count(),
            "brands": BrandProfile.query.count(),
        })

    @app.post("/api/auth/register")
    @limiter.limit("5 per minute")
    def api_register():
        data = json_object()
        if data is None:
            return jsonify(error="Expected a JSON object"), 400
        error = required_fields(data, ["name", "email", "password", "role"])
        email = str(data.get("email", "")).strip().lower()
        password = data.get("password") if isinstance(data.get("password"), str) else ""
        name = data.get("name") if isinstance(data.get("name"), str) else ""
        role = data.get("role") if isinstance(data.get("role"), str) else ""
        if error or not name.strip() or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(password) < 8 or role not in ROLES:
            return jsonify(error=error or "Invalid email, role, or password (minimum 8 characters)"), 400
        if User.query.filter_by(email=email).first():
            return jsonify(error="Account already exists"), 409
        user = User(name=name.strip(), email=email, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        if user.role == "creator":
            db.session.add(CreatorProfile(user_id=user.id, username=f"user{user.id}"))
        else:
            db.session.add(BrandProfile(user_id=user.id, company_name=user.name))
        db.session.commit()
        token = create_access_token(identity=str(user.id), additional_claims={"role": user.role})
        return jsonify(access_token=token, user={"id": user.id, "name": user.name, "role": user.role}), 201

    @app.post("/api/auth/login")
    @limiter.limit("5 per minute")
    def api_login():
        data = json_object()
        if data is None:
            return jsonify(error="Expected a JSON object"), 400
        email = data.get("email") if isinstance(data.get("email"), str) else ""
        user = User.query.filter_by(email=email.strip().lower()).first()
        password = data.get("password") if isinstance(data.get("password"), str) else ""
        if not user or not user.check_password(password):
            return jsonify(error="Email or password is incorrect"), 401
        token = create_access_token(identity=str(user.id), additional_claims={"role": user.role})
        return jsonify(access_token=token, user={"id": user.id, "name": user.name, "role": user.role})

    @app.post("/api/auth/logout")
    @jwt_required()
    def api_logout():
        token_id = get_jwt()["jti"]
        db.session.add(RevokedToken(jti=token_id))
        db.session.commit()
        return jsonify(message="Logged out")

    @app.get("/api/auth/me")
    @jwt_required()
    def api_me():
        user = db.session.get(User, int(get_jwt_identity()))
        if not user:
            return jsonify(error="User no longer exists"), 404
        return jsonify(id=user.id, name=user.name, email=user.email, role=user.role)

    @app.get("/api/brands/<int:brand_id>")
    def api_brand_profile(brand_id):
        user = db.session.get(User, brand_id)
        if not user or user.role not in {"brand", "agency"} or not user.brand_profile:
            return jsonify(error="Brand not found"), 404
        profile = user.brand_profile
        return jsonify(id=user.id, name=user.name, company_name=profile.company_name, industry=profile.industry,
                       description=profile.description, location=profile.location)

    @app.post("/api/brands/profile")
    @app.put("/api/brands/profile")
    @api_role_required("brand", "agency")
    def api_update_brand_profile():
        user = db.session.get(User, int(get_jwt_identity()))
        profile = user.brand_profile
        data = json_object()
        if data is None:
            return jsonify(error="Expected a JSON object"), 400
        for field in ("company_name", "industry", "description", "location"):
            if field in data:
                value = data[field]
                if not isinstance(value, str):
                    return jsonify(error=f"{field} must be text"), 400
                if field == "company_name" and not value.strip():
                    return jsonify(error="Company name is required"), 400
                setattr(profile, field, value.strip()[:1000])
        db.session.commit()
        return jsonify(id=user.id, company_name=profile.company_name, industry=profile.industry,
                       description=profile.description, location=profile.location)

    @app.get("/api/creators")
    def api_creators():
        query = CreatorProfile.query.join(User)
        category = request.args.get("category", "").strip()
        search = request.args.get("q", "").strip()
        if category:
            query = query.filter(CreatorProfile.category.ilike(f"%{category}%"))
        if search:
            query = query.filter(db.or_(User.name.ilike(f"%{search}%"), CreatorProfile.username.ilike(f"%{search}%")))
        try:
            page = max(1, int(request.args.get("page", 1)))
            per_page = max(1, min(100, int(request.args.get("per_page", 20))))
        except ValueError:
            return jsonify(error="page and per_page must be integers"), 400
        pagination = query.order_by(User.name).paginate(page=page, per_page=per_page, error_out=False)
        return jsonify({"items": [serialize_creator(profile) for profile in pagination.items],
                        "page": pagination.page, "per_page": pagination.per_page,
                        "total": pagination.total, "pages": pagination.pages})

    @app.post("/api/creators/profile")
    @app.put("/api/creators/profile")
    @api_role_required("creator")
    def api_creator_profile():
        user = db.session.get(User, int(get_jwt_identity()))
        profile = user.creator_profile
        data = json_object()
        if data is None:
            return jsonify(error="Expected a JSON object"), 400
        for field in ("bio", "location", "category", "audience"):
            if field in data:
                if not isinstance(data[field], str):
                    return jsonify(error=f"{field} must be text"), 400
                setattr(profile, field, data[field].strip()[:1000])
        for field in ("categories", "skills", "formats"):
            if field in data:
                if not isinstance(data[field], list) or len(data[field]) > 30:
                    return jsonify(error=f"{field} must be a list of at most 30 items"), 400
                if any(not isinstance(item, str) for item in data[field]):
                    return jsonify(error=f"{field} items must be text"), 400
                setattr(profile, field, [item.strip()[:80] for item in data[field] if item.strip()])
        for field in ("minimum_budget", "maximum_budget", "followers", "engagement_rate"):
            if field in data:
                try:
                    value = float(data[field]) if data[field] is not None else None
                    if value is not None and (not math.isfinite(value) or value < 0):
                        raise ValueError()
                    if field == "followers" and value is not None and not value.is_integer():
                        raise ValueError()
                    if field == "engagement_rate" and value is not None and value > 100:
                        raise ValueError()
                    setattr(profile, field, int(value) if field == "followers" and value is not None else value)
                except (TypeError, ValueError):
                    return jsonify(error=f"{field} must be a non-negative number"), 400
        if profile.minimum_budget and profile.maximum_budget and profile.maximum_budget < profile.minimum_budget:
            return jsonify(error="Maximum budget must be at least the minimum budget"), 400
        if "available" in data:
            if not isinstance(data["available"], bool):
                return jsonify(error="available must be true or false"), 400
            profile.available = data["available"]
        db.session.commit()
        return jsonify(serialize_creator(profile))

    @app.get("/api/campaigns")
    def api_campaigns():
        query = Campaign.query.filter_by(status="published")
        category = request.args.get("category", "").strip()
        if category:
            query = query.filter(Campaign.category.ilike(f"%{category}%"))
        try:
            page = max(1, int(request.args.get("page", 1)))
            per_page = max(1, min(100, int(request.args.get("per_page", 20))))
        except ValueError:
            return jsonify(error="page and per_page must be integers"), 400
        pagination = query.order_by(Campaign.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
        return jsonify({"items": [serialize_campaign(item) for item in pagination.items],
                        "page": pagination.page, "per_page": pagination.per_page,
                        "total": pagination.total, "pages": pagination.pages})

    @app.get("/api/campaigns/<int:campaign_id>")
    @jwt_required(optional=True)
    def api_campaign_detail(campaign_id):
        campaign = db.session.get(Campaign, campaign_id)
        if not campaign:
            return jsonify(error="Campaign not found"), 404
        if campaign.status != "published":
            if not get_jwt_identity() or int(get_jwt_identity()) != campaign.owner_id:
                return jsonify(error="Campaign not found"), 404
        return jsonify(serialize_campaign(campaign))

    @app.post("/api/campaigns")
    @api_role_required("brand", "agency")
    def api_create_campaign():
        data = json_object()
        if data is None:
            return jsonify(error="Expected a JSON object"), 400
        error = required_fields(data, ["title", "description", "category", "target_audience", "content_format"])
        try:
            minimum = float(data.get("minimum_budget"))
            maximum = float(data.get("maximum_budget"))
        except (TypeError, ValueError):
            minimum = maximum = -1
        if error or not math.isfinite(minimum) or not math.isfinite(maximum) or minimum < 0 or maximum < minimum:
            return jsonify(error=error or "Enter a valid campaign budget range"), 400
        if "publish" in data and not isinstance(data["publish"], bool):
            return jsonify(error="publish must be true or false"), 400
        raw_creators_required = data.get("creators_required", 1)
        if isinstance(raw_creators_required, bool) or not (
            isinstance(raw_creators_required, int)
            or (isinstance(raw_creators_required, str) and raw_creators_required.isdigit())
        ):
            return jsonify(error="creators_required must be between 1 and 100"), 400
        creators_required = int(raw_creators_required)
        if not 1 <= creators_required <= 100:
            return jsonify(error="creators_required must be between 1 and 100"), 400
        allow_negotiation = data.get("allow_negotiation", False)
        if not isinstance(allow_negotiation, bool):
            return jsonify(error="allow_negotiation must be true or false"), 400
        campaign = Campaign(
            owner_id=int(get_jwt_identity()),
            title=str(data["title"]).strip()[:180],
            description=str(data["description"]).strip(),
            category=str(data["category"]).strip()[:80],
            objective=str(data.get("objective", ""))[:120],
            target_audience=str(data["target_audience"])[:200],
            content_format=str(data["content_format"])[:100],
            minimum_budget=minimum,
            maximum_budget=maximum,
            creators_required=creators_required,
            status="published" if data.get("publish", True) else "draft",
            allow_negotiation=allow_negotiation,
        )
        db.session.add(campaign)
        db.session.commit()
        return jsonify(serialize_campaign(campaign)), 201

    @app.put("/api/campaigns/<int:campaign_id>")
    @api_role_required("brand", "agency")
    def api_update_campaign(campaign_id):
        campaign = db.session.get(Campaign, campaign_id)
        if not campaign:
            return jsonify(error="Campaign not found"), 404
        if campaign.owner_id != int(get_jwt_identity()):
            return jsonify(error="You do not own this campaign"), 403
        data = json_object()
        if data is None:
            return jsonify(error="Expected a JSON object"), 400
        for field in ("title", "description", "category", "objective", "target_audience", "content_format"):
            if field in data:
                if not isinstance(data[field], str):
                    return jsonify(error=f"{field} must be text"), 400
                value = str(data[field]).strip()
                if field in {"title", "description", "category", "target_audience", "content_format"} and not value:
                    return jsonify(error=f"{field} cannot be empty"), 400
                setattr(campaign, field, value)
        for field in ("minimum_budget", "maximum_budget"):
            if field in data:
                try:
                    value = float(data[field])
                except (TypeError, ValueError):
                    return jsonify(error=f"{field} must be a non-negative number"), 400
                if not math.isfinite(value) or value < 0:
                    return jsonify(error=f"{field} must be a non-negative number"), 400
                setattr(campaign, field, value)
        if campaign.maximum_budget < campaign.minimum_budget:
            return jsonify(error="Maximum budget must be at least the minimum budget"), 400
        if "creators_required" in data:
            value = data["creators_required"]
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
                return jsonify(error="creators_required must be between 1 and 100"), 400
            campaign.creators_required = value
        if "publish" in data:
            if not isinstance(data["publish"], bool):
                return jsonify(error="publish must be true or false"), 400
            campaign.status = "published" if data["publish"] else "draft"
        if "status" in data:
            if not isinstance(data["status"], str) or data["status"] not in {"draft", "published", "paused", "completed", "cancelled"}:
                return jsonify(error="Invalid campaign status"), 400
            campaign.status = data["status"]
        if "allow_negotiation" in data:
            if not isinstance(data["allow_negotiation"], bool):
                return jsonify(error="allow_negotiation must be true or false"), 400
            campaign.allow_negotiation = data["allow_negotiation"]
        db.session.commit()
        return jsonify(serialize_campaign(campaign))

    @app.delete("/api/campaigns/<int:campaign_id>")
    @api_role_required("brand", "agency")
    def api_delete_campaign(campaign_id):
        campaign = db.session.get(Campaign, campaign_id)
        if not campaign:
            return jsonify(error="Campaign not found"), 404
        if campaign.owner_id != int(get_jwt_identity()):
            return jsonify(error="You do not own this campaign"), 403
        if campaign.status != "draft" or Application.query.filter_by(campaign_id=campaign.id).first():
            return jsonify(error="Only un-applied draft campaigns may be deleted"), 409
        db.session.delete(campaign)
        db.session.commit()
        return jsonify(deleted=True)

    @app.get("/api/campaigns/<int:campaign_id>/recommendations")
    @api_role_required("brand", "agency")
    def api_creator_recommendations(campaign_id):
        campaign = db.session.get(Campaign, campaign_id)
        user_id = int(get_jwt_identity())
        if not campaign:
            return jsonify(error="Campaign not found"), 404
        if campaign.owner_id != user_id:
            return jsonify(error="You do not own this campaign"), 403
        creators = [creator_match_dict(item) for item in CreatorProfile.query.filter_by(available=True).all()]
        ranked = rank_creators(creators, serialize_campaign(campaign))
        return jsonify(ranked)

    @app.post("/api/campaigns/<int:campaign_id>/apply")
    @api_role_required("creator")
    def api_apply(campaign_id):
        campaign = db.session.get(Campaign, campaign_id)
        if not campaign or campaign.status != "published":
            return jsonify(error="Published campaign not found"), 404
        data = json_object()
        if data is None:
            return jsonify(error="Expected a JSON object"), 400
        message = data.get("message", "")
        if not isinstance(message, str):
            return jsonify(error="message must be text"), 400
        application = Application(campaign_id=campaign_id, creator_id=int(get_jwt_identity()), message=message[:2000])
        db.session.add(application)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify(error="You have already applied to this campaign"), 409
        return jsonify(id=application.id, status=application.status, campaign_id=campaign_id), 201

    @app.get("/api/applications/my")
    @jwt_required()
    def api_my_applications():
        user_id = int(get_jwt_identity())
        user = db.session.get(User, user_id)
        query = Application.query.join(Campaign)
        if user.role == "creator":
            query = query.filter(Application.creator_id == user_id)
        else:
            query = query.filter(Campaign.owner_id == user_id)
        return jsonify([{
            "id": item.id,
            "campaign_id": item.campaign_id,
            "campaign_title": item.campaign.title,
            "creator_name": item.creator.name,
            "status": item.status,
            "message": item.message,
        } for item in query.order_by(Application.created_at.desc()).all()])

    @app.delete("/api/applications/<int:application_id>")
    @api_role_required("creator")
    def api_withdraw_application(application_id):
        application = db.session.get(Application, application_id)
        if not application or application.creator_id != int(get_jwt_identity()):
            return jsonify(error="Application not found"), 404
        if application.status not in {"Pending", "Shortlisted"}:
            return jsonify(error="This application can no longer be withdrawn"), 409
        application.status = "Withdrawn"
        db.session.commit()
        return jsonify(id=application.id, status=application.status)

    @app.put("/api/applications/<int:application_id>/status")
    @api_role_required("brand", "agency")
    def api_update_application_status(application_id):
        application = db.session.get(Application, application_id)
        if not application:
            return jsonify(error="Application not found"), 404
        user_id = int(get_jwt_identity())
        if application.campaign.owner_id != user_id:
            return jsonify(error="You do not own this application"), 403
        data = json_object()
        if data is None:
            return jsonify(error="Expected a JSON object"), 400
        status = data.get("status")
        if not isinstance(status, str) or status not in APPLICATION_STATUSES - {"Withdrawn"}:
            return jsonify(error="Invalid application status"), 400
        application.status = status
        db.session.commit()
        return jsonify(id=application.id, status=application.status)

    @app.get("/api/recommendations/campaigns")
    @api_role_required("creator")
    def api_campaign_recommendations():
        user = db.session.get(User, int(get_jwt_identity()))
        if not user.creator_profile:
            return jsonify([])
        return jsonify(rank_campaigns_for_creator(user.creator_profile, Campaign.query.filter_by(status="published").all()))

    @app.post("/api/creators/portfolio")
    @api_role_required("creator")
    def api_portfolio_upload():
        user = db.session.get(User, int(get_jwt_identity()))
        try:
            text = extract_portfolio_text(request.files.get("portfolio"), app.config["MAX_CONTENT_LENGTH"])
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        user.creator_profile.portfolio_text = text
        db.session.commit()
        return jsonify(extracted_text=text, suggestions=suggest_profile_fields(text), requires_review=True)

    @app.post("/api/creators/<int:creator_id>/save")
    @api_role_required("brand", "agency")
    def api_save_creator(creator_id):
        creator = db.session.get(User, creator_id)
        if not creator or creator.role != "creator":
            return jsonify(error="Creator not found"), 404
        item = SavedCreator(brand_id=int(get_jwt_identity()), creator_id=creator_id)
        db.session.add(item)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
        return jsonify(saved=True), 201

    @app.delete("/api/creators/<int:creator_id>/save")
    @api_role_required("brand", "agency")
    def api_unsave_creator(creator_id):
        SavedCreator.query.filter_by(brand_id=int(get_jwt_identity()), creator_id=creator_id).delete()
        db.session.commit()
        return jsonify(saved=False)

    @app.post("/api/campaigns/<int:campaign_id>/save")
    @api_role_required("creator")
    def api_save_campaign(campaign_id):
        campaign = db.session.get(Campaign, campaign_id)
        if not campaign or campaign.status != "published":
            return jsonify(error="Campaign not found"), 404
        item = SavedCampaign(creator_id=int(get_jwt_identity()), campaign_id=campaign_id)
        db.session.add(item)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
        return jsonify(saved=True), 201

    @app.delete("/api/campaigns/<int:campaign_id>/save")
    @api_role_required("creator")
    def api_unsave_campaign(campaign_id):
        SavedCampaign.query.filter_by(creator_id=int(get_jwt_identity()), campaign_id=campaign_id).delete()
        db.session.commit()
        return jsonify(saved=False)

    @app.post("/api/brief/generate")
    @api_role_required("brand", "agency")
    def api_brief_generate():
        data = json_object()
        if data is None:
            return jsonify(error="Expected a JSON object"), 400
        category = str(data.get("industry", "Lifestyle")).strip()
        product = str(data.get("product", "New campaign")).strip()
        audience = str(data.get("audience", "Interested audiences")).strip()
        content_format = str(data.get("content_format", "Short-form video")).strip()
        return jsonify({
            "is_draft": True,
            "title": f"{category} campaign: {product}",
            "summary": f"Introduce {product} to {audience} through creator-led {content_format.lower()}.",
            "objective": "Awareness and engagement",
            "deliverables": ["1 short-form video", "2 story frames per creator"],
            "timeline": "2 weeks: briefing, production, review, publication",
            "suggested_indicators": ["Reach", "Video views", "Saves", "Qualified clicks"],
        })

    @app.get("/api/analytics/creator")
    @api_role_required("creator")
    def api_creator_analytics():
        creator_id = int(get_jwt_identity())
        statuses = db.session.query(Application.status, db.func.count(Application.id)).filter_by(creator_id=creator_id).group_by(Application.status).all()
        return jsonify({
            "applications_submitted": sum(count for _status, count in statuses),
            "applications_by_status": {status: count for status, count in statuses},
        })

    @app.get("/api/analytics/brand")
    @api_role_required("brand", "agency")
    def api_brand_analytics():
        brand_id = int(get_jwt_identity())
        campaigns = Campaign.query.filter_by(owner_id=brand_id).all()
        application_counts = db.session.query(Application.status, db.func.count(Application.id)).join(Campaign).filter(Campaign.owner_id == brand_id).group_by(Application.status).all()
        return jsonify({
            "campaigns_created": len(campaigns),
            "campaigns_by_status": {status: sum(1 for item in campaigns if item.status == status) for status in {item.status for item in campaigns}},
            "applications_received": sum(count for _status, count in application_counts),
            "applications_by_status": {status: count for status, count in application_counts},
        })

    @app.cli.command("seed-demo")
    def seed_demo_command():
        """Insert clearly identified fictional local marketplace records."""
        seed_demo_data()
        print("Demo data is ready.")

    @app.errorhandler(413)
    def too_large(_error):
        limit = app.config.get("MAX_CONTENT_LENGTH")
        limit_mb = max(1, limit // (1024 * 1024)) if isinstance(limit, int) else "configured"
        if request.path.startswith("/api/"):
            return jsonify(error=f"Upload exceeds the {limit_mb} MB request limit"), 413
        flash(f"Upload exceeds the {limit_mb} MB request limit.", "error")
        if request.path == "/projects/new":
            return redirect(url_for("create_project"))
        edit_match = re.fullmatch(r"/projects/(\d+)/edit", request.path)
        if edit_match:
            return redirect(url_for("edit_project", project_id=int(edit_match.group(1))))
        return redirect(url_for("profile"))

    @app.errorhandler(429)
    def too_many_requests(_error):
        if request.path.startswith("/api/"):
            return jsonify(error="Too many authentication attempts. Try again later."), 429
        flash("Too many attempts. Try again in a minute.", "error")
        return redirect(url_for("login"))

    return app


def seed_demo_data():
    if User.query.first():
        return
    creators = [
        ("Aarohi Mehta", "aarohi", "Fashion", ["Fashion", "Lifestyle"], ["Styling", "Beauty"], ["Reels", "Stories"], "College students", 22000, 50000, True, 185000, 8.4),
        ("Rayan Shah", "rayan", "Technology", ["Technology", "Education"], ["Software", "Product demos"], ["Short video", "Reviews"], "Software enthusiasts", 40000, 90000, True, 240000, 7.8),
        ("Neha Kapoor", "neha", "Travel", ["Travel", "Food"], ["Travel guides"], ["Reels", "Vlogs"], "Young professionals", 25000, 60000, True, 142000, 9.2),
        ("Ishaan Rao", "ishaan", "Food", ["Food", "Lifestyle"], ["Recipes"], ["Reels", "Stories"], "Local food lovers", 12000, 35000, True, 78000, 6.9),
        ("Mira Joshi", "mira", "Fitness", ["Fitness", "Wellness"], ["Training", "Nutrition"], ["Short video", "Stories"], "Fitness audiences", 18000, 45000, True, 112000, 8.1),
        ("Dev Malhotra", "dev", "Gaming", ["Gaming", "Technology"], ["Streaming", "Reviews"], ["Live", "Short video"], "Gaming fans", 30000, 75000, True, 205000, 7.2),
        ("Sara Khan", "sara", "Beauty", ["Beauty", "Fashion"], ["Skincare", "Makeup"], ["Reels", "Stories"], "Beauty shoppers", 20000, 55000, True, 156000, 9.1),
        ("Kabir Sen", "kabir", "Photography", ["Travel", "Photography"], ["Photography"], ["Vlogs", "Stories"], "Travel enthusiasts", 15000, 40000, False, 90000, 6.8),
        ("Tara Das", "tara", "Parenting", ["Parenting", "Food"], ["Family life"], ["Reels", "Blogs"], "Young families", 16000, 42000, True, 98000, 7.5),
        ("Arjun Pillai", "arjun", "Finance", ["Finance", "Technology"], ["Personal finance"], ["Short video", "Blogs"], "Young professionals", 35000, 85000, True, 176000, 6.5),
        ("Zoya Ali", "zoya", "Fashion", ["Fashion", "Travel"], ["Sustainable style"], ["Reels", "Photos"], "College students", 24000, 62000, True, 132000, 8.8),
        ("Nikhil Verma", "nikhil", "Food", ["Food", "Travel"], ["Local cuisine"], ["Reels", "Vlogs"], "Local food lovers", 14000, 38000, True, 84000, 8.0),
    ]
    creator_users = []
    for name, username, category, categories, skills, formats, audience, minimum, maximum, available, followers, engagement in creators:
        user = User(name=name, email=f"{username}@demo.creatorbridge.test", role="creator")
        user.set_password("DemoPass123!")
        db.session.add(user)
        db.session.flush()
        db.session.add(CreatorProfile(
            user_id=user.id, username=username, category=category, categories=categories, skills=skills, formats=formats,
            audience=audience, minimum_budget=minimum, maximum_budget=maximum, available=available,
            followers=followers, engagement_rate=engagement, location="India",
            bio=f"Demonstration creator profile focused on {category.lower()} content.",
        ))
        creator_users.append(user)
    brands = [
        ("NovaFit", "Fitness"), ("PixelNest", "Technology"), ("Harbor & Co.", "Travel"), ("Mango & Mint", "Food")
    ]
    brand_users = []
    for name, industry in brands:
        user = User(name=name, email=f"{name.lower().replace(' ', '').replace('&', 'and')}@demo.creatorbridge.test", role="brand")
        user.set_password("DemoPass123!")
        db.session.add(user)
        db.session.flush()
        db.session.add(BrandProfile(user_id=user.id, company_name=name, industry=industry, description="Sample brand used for local demonstrations."))
        brand_users.append(user)
    campaign_data = [
        ("Campus Wellness Sprint", "Fitness", "Brand awareness", "College students", "Reels", 15000, 45000),
        ("Creator-Tech Launch", "Technology", "Product education", "Software enthusiasts", "Short video", 35000, 80000),
        ("Explore India Reels", "Travel", "Engagement", "Young professionals", "Reels", 20000, 60000),
        ("Local Flavours Week", "Food", "Store visits", "Local food lovers", "Reels", 10000, 35000),
        ("Everyday Style Edit", "Fashion", "Brand awareness", "College students", "Stories", 20000, 55000),
        ("Smart Money Basics", "Finance", "Education", "Young professionals", "Short video", 30000, 75000),
        ("Clean Beauty Stories", "Beauty", "Product education", "Beauty shoppers", "Stories", 18000, 50000),
        ("Family Table", "Food", "Engagement", "Young families", "Blogs", 14000, 40000),
        ("Weekend Adventure", "Travel", "Awareness", "Travel enthusiasts", "Vlogs", 25000, 65000),
        ("Game On", "Gaming", "Product awareness", "Gaming fans", "Live", 30000, 80000),
    ]
    for index, item in enumerate(campaign_data):
        title, category, objective, audience, content_format, minimum, maximum = item
        owner = brand_users[index % len(brand_users)]
        db.session.add(Campaign(
            owner_id=owner.id, title=title, description=f"Demonstration campaign for {category.lower()} creators.",
            category=category, objective=objective, target_audience=audience, content_format=content_format,
            minimum_budget=minimum, maximum_budget=maximum, status="published",
        ))
    db.session.commit()


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=int(os.environ.get("PORT", "5000")))
