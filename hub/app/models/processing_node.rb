# One Altair installation (a processing PC). It serves one or more telescopes.
class ProcessingNode < ApplicationRecord
  has_many :processing_node_telescopes, dependent: :destroy
  has_many :telescopes, through: :processing_node_telescopes
  has_many :api_keys, as: :owner, dependent: :destroy
  has_many :frames, dependent: :nullify
  has_many :processing_issues, dependent: :destroy
  has_many :processing_jobs, dependent: :delete_all
  has_many :processing_commands, dependent: :delete_all
  has_many :calibration_masters, dependent: :delete_all

  validates :name, presence: true, uniqueness: true, format: { with: /\A[a-z0-9_\-]+\z/ }

  scope :active, -> { where(active: true) }

  def to_param
    name
  end

  HEARTBEAT_STALE_AFTER = 30.minutes

  def healthy?
    last_heartbeat_at.present? && last_heartbeat_at > HEARTBEAT_STALE_AFTER.ago
  end
end
