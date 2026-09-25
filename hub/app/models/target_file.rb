class TargetFile < ApplicationRecord
  belongs_to :target

  enum :kind, { sub: 0, stacked: 1, preview: 2, log: 3 }

  # The URL comes from the worker API and is rendered as a link, so only
  # http(s) is allowed (no javascript: or data: URLs).
  validates :url, presence: true, format: { with: %r{\Ahttps?://\S+\z}i, message: "must be an http(s) URL" }

  scope :recent_first, -> { order(captured_at: :desc, created_at: :desc) }
end
