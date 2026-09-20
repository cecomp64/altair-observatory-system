class ApiKey < ApplicationRecord
  belongs_to :telescope

  validates :name, presence: true
  validates :token_digest, presence: true, uniqueness: true

  scope :active, -> { where(active: true) }

  # Set only right after `generate_token!`, never persisted or reloaded.
  attr_reader :plaintext_token

  # Generates a new random token, stores its digest, and stashes the
  # plaintext on the instance (via `plaintext_token`) so the caller can
  # show it to the admin exactly once.
  def generate_token!
    raw = SecureRandom.hex(24)
    @plaintext_token = raw
    self.token_digest = self.class.digest(raw)
    raw
  end

  def self.digest(raw_token)
    Digest::SHA256.hexdigest(raw_token)
  end

  def self.authenticate(raw_token)
    return nil if raw_token.blank?

    active.find_by(token_digest: digest(raw_token))
  end

  def touch_last_used!
    update_column(:last_used_at, Time.current)
  end
end
