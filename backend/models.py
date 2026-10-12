from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from backend.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    creator_profile = db.relationship("CreatorProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    brand_profile = db.relationship("BrandProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class CreatorProfile(db.Model):
    __tablename__ = "creator_profiles"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    bio = db.Column(db.Text, default="", nullable=False)
    location = db.Column(db.String(120), default="", nullable=False)
    category = db.Column(db.String(80), default="", nullable=False)
    categories = db.Column(db.JSON, default=list, nullable=False)
    skills = db.Column(db.JSON, default=list, nullable=False)
    formats = db.Column(db.JSON, default=list, nullable=False)
    audience = db.Column(db.String(200), default="", nullable=False)
    followers = db.Column(db.Integer)
    engagement_rate = db.Column(db.Float)
    minimum_budget = db.Column(db.Float)
    maximum_budget = db.Column(db.Float)
    available = db.Column(db.Boolean, default=True, nullable=False)
    portfolio_text = db.Column(db.Text, default="", nullable=False)
    user = db.relationship("User", back_populates="creator_profile")


class BrandProfile(db.Model):
    __tablename__ = "brand_profiles"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    company_name = db.Column(db.String(160), nullable=False)
    industry = db.Column(db.String(100), default="", nullable=False)
    description = db.Column(db.Text, default="", nullable=False)
    location = db.Column(db.String(120), default="", nullable=False)
    user = db.relationship("User", back_populates="brand_profile")


class Campaign(db.Model):
    __tablename__ = "campaigns"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(80), nullable=False)
    objective = db.Column(db.String(120), default="", nullable=False)
    target_audience = db.Column(db.String(200), default="", nullable=False)
    content_format = db.Column(db.String(100), default="", nullable=False)
    minimum_budget = db.Column(db.Float, nullable=False)
    maximum_budget = db.Column(db.Float, nullable=False)
    creators_required = db.Column(db.Integer, default=1, nullable=False)
    status = db.Column(db.String(30), default="published", nullable=False, index=True)
    allow_negotiation = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    owner = db.relationship("User")


class Application(db.Model):
    __tablename__ = "campaign_applications"
    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaigns.id", ondelete="RESTRICT"), nullable=False)
    creator_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    message = db.Column(db.Text, default="", nullable=False)
    status = db.Column(db.String(30), default="Pending", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    campaign = db.relationship("Campaign")
    creator = db.relationship("User")
    __table_args__ = (db.UniqueConstraint("campaign_id", "creator_id", name="uq_application_campaign_creator"),)


class SavedCreator(db.Model):
    __tablename__ = "saved_creators"
    id = db.Column(db.Integer, primary_key=True)
    brand_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    creator_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    __table_args__ = (db.UniqueConstraint("brand_id", "creator_id", name="uq_saved_creator"),)


class SavedCampaign(db.Model):
    __tablename__ = "saved_campaigns"
    id = db.Column(db.Integer, primary_key=True)
    creator_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    campaign_id = db.Column(db.Integer, db.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    __table_args__ = (db.UniqueConstraint("creator_id", "campaign_id", name="uq_saved_campaign"),)


class RevokedToken(db.Model):
    __tablename__ = "revoked_tokens"
    id = db.Column(db.Integer, primary_key=True)
    jti = db.Column(db.String(36), unique=True, nullable=False, index=True)


class Project(db.Model):
    __tablename__ = "portfolio_projects"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, default="", nullable=False)
    project_type = db.Column(db.String(32), nullable=False, index=True)
    category = db.Column(db.String(100), default="", nullable=False, index=True)
    tags = db.Column(db.JSON, default=list, nullable=False)
    client_name = db.Column(db.String(160), default="", nullable=False)
    credits = db.Column(db.Text, default="", nullable=False)
    location = db.Column(db.String(160), default="", nullable=False)
    project_date = db.Column(db.Date, nullable=True)
    tools = db.Column(db.Text, default="", nullable=False)
    creative_process = db.Column(db.Text, default="", nullable=False)
    target_audience = db.Column(db.String(240), default="", nullable=False)
    campaign_objective = db.Column(db.Text, default="", nullable=False)
    campaign_results = db.Column(db.Text, default="", nullable=False)
    external_video_url = db.Column(db.String(500), default="", nullable=False)
    visibility = db.Column(db.String(16), default="private", nullable=False, index=True)
    status = db.Column(db.String(16), default="draft", nullable=False, index=True)
    progress = db.Column(db.String(32), default="completed", nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    user = db.relationship("User")
    media = db.relationship(
        "ProjectMedia",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectMedia.display_order, ProjectMedia.id",
    )
    likes = db.relationship("ProjectLike", back_populates="project", cascade="all, delete-orphan")
    bookmarks = db.relationship("ProjectBookmark", back_populates="project", cascade="all, delete-orphan")
    analytics_events = db.relationship("ProjectAnalyticsEvent", back_populates="project", cascade="all, delete-orphan")


class ProjectMedia(db.Model):
    __tablename__ = "portfolio_project_media"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("portfolio_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    storage_name = db.Column(db.String(255), unique=True, nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    media_type = db.Column(db.String(16), nullable=False)
    mime_type = db.Column(db.String(100), nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    display_order = db.Column(db.Integer, default=0, nullable=False)
    caption = db.Column(db.String(240), default="", nullable=False)
    is_cover = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    project = db.relationship("Project", back_populates="media")


class ProjectLike(db.Model):
    __tablename__ = "portfolio_project_likes"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("portfolio_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    project = db.relationship("Project", back_populates="likes")
    __table_args__ = (db.UniqueConstraint("project_id", "user_id", name="uq_portfolio_project_like"),)


class ProjectBookmark(db.Model):
    __tablename__ = "portfolio_project_bookmarks"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("portfolio_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    project = db.relationship("Project", back_populates="bookmarks")
    __table_args__ = (db.UniqueConstraint("project_id", "user_id", name="uq_portfolio_project_bookmark"),)


class ProjectAnalyticsEvent(db.Model):
    __tablename__ = "portfolio_project_analytics"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("portfolio_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = db.Column(db.String(24), nullable=False, index=True)
    visitor_key = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    project = db.relationship("Project", back_populates="analytics_events")
    __table_args__ = (
        db.UniqueConstraint("project_id", "event_type", "visitor_key", name="uq_portfolio_analytics_visitor_event"),
    )
