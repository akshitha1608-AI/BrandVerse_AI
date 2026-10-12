CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(120) NOT NULL,
  email VARCHAR(255) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  role VARCHAR(20) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX ix_users_email (email)
);

CREATE TABLE creator_profiles (
  id INTEGER PRIMARY KEY AUTO_INCREMENT,
  user_id INTEGER NOT NULL UNIQUE,
  username VARCHAR(80) NOT NULL UNIQUE,
  bio TEXT NOT NULL,
  location VARCHAR(120) NOT NULL,
  category VARCHAR(80) NOT NULL,
  categories JSON NOT NULL,
  skills JSON NOT NULL,
  formats JSON NOT NULL,
  audience VARCHAR(200) NOT NULL,
  followers INTEGER NULL,
  engagement_rate FLOAT NULL,
  minimum_budget DECIMAL(12,2) NULL,
  maximum_budget DECIMAL(12,2) NULL,
  available BOOLEAN NOT NULL DEFAULT TRUE,
  portfolio_text TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE brand_profiles (
  id INTEGER PRIMARY KEY AUTO_INCREMENT,
  user_id INTEGER NOT NULL UNIQUE,
  company_name VARCHAR(160) NOT NULL,
  industry VARCHAR(100) NOT NULL,
  description TEXT NOT NULL,
  location VARCHAR(120) NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE campaigns (
  id INTEGER PRIMARY KEY AUTO_INCREMENT,
  owner_id INTEGER NOT NULL,
  title VARCHAR(180) NOT NULL,
  description TEXT NOT NULL,
  category VARCHAR(80) NOT NULL,
  objective VARCHAR(120) NOT NULL,
  target_audience VARCHAR(200) NOT NULL,
  content_format VARCHAR(100) NOT NULL,
  minimum_budget DECIMAL(12,2) NOT NULL,
  maximum_budget DECIMAL(12,2) NOT NULL,
  creators_required INTEGER NOT NULL DEFAULT 1,
  status VARCHAR(30) NOT NULL DEFAULT 'published',
  allow_negotiation BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX ix_campaigns_owner (owner_id),
  INDEX ix_campaigns_status (status),
  FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE TABLE campaign_applications (
  id INTEGER PRIMARY KEY AUTO_INCREMENT,
  campaign_id INTEGER NOT NULL,
  creator_id INTEGER NOT NULL,
  message TEXT NOT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'Pending',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_application_campaign_creator (campaign_id, creator_id),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE RESTRICT,
  FOREIGN KEY (creator_id) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE TABLE saved_creators (
  id INTEGER PRIMARY KEY AUTO_INCREMENT,
  brand_id INTEGER NOT NULL,
  creator_id INTEGER NOT NULL,
  UNIQUE KEY uq_saved_creator (brand_id, creator_id),
  FOREIGN KEY (brand_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (creator_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE saved_campaigns (
  id INTEGER PRIMARY KEY AUTO_INCREMENT,
  creator_id INTEGER NOT NULL,
  campaign_id INTEGER NOT NULL,
  UNIQUE KEY uq_saved_campaign (creator_id, campaign_id),
  FOREIGN KEY (creator_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE revoked_tokens (
  id INTEGER PRIMARY KEY AUTO_INCREMENT,
  jti VARCHAR(36) NOT NULL UNIQUE,
  INDEX ix_revoked_tokens_jti (jti)
);
