# An API key for a worker (owned by a Telescope) or an Altair processing node
# (owned by a ProcessingNode), with the scopes it may use (§5.1).
class ApiKey < ApplicationRecord
  SCOPES = %w[
    targets:read progress:write events:write sessions:write files:write
    frames:write products:write issues:write commands:read heartbeat:write
  ].freeze
  DEFAULT_SCOPES = {
    "Telescope" => %w[targets:read progress:write events:write sessions:write files:write heartbeat:write],
    "ProcessingNode" => %w[targets:read events:write frames:write products:write issues:write commands:read heartbeat:write]
  }.freeze

  belongs_to :owner, polymorphic: true

  before_validation :default_scopes, on: :create

  validates :name, presence: true
  validates :token_digest, presence: true, uniqueness: true
  validates :owner_type, inclusion: { in: DEFAULT_SCOPES.keys }
  validate :scopes_are_known

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

  def allows?(scope)
    scopes.include?(scope.to_s)
  end

  def telescope
    owner if owner_type == "Telescope"
  end

  def processing_node
    owner if owner_type == "ProcessingNode"
  end

  # Telescopes this key may act on: its own telescope, or the telescopes its
  # processing node serves.
  def telescope_ids
    telescope ? [ telescope.id ] : processing_node.telescope_ids
  end

  def may_access_telescope?(telescope)
    telescope_ids.include?(telescope.id)
  end

  private

  def default_scopes
    self.scopes = DEFAULT_SCOPES.fetch(owner_type, []) if scopes.blank?
  end

  def scopes_are_known
    unknown = scopes - SCOPES
    errors.add(:scopes, "include unknown scopes: #{unknown.join(', ')}") if unknown.any?
  end
end
