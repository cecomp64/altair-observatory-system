class TargetFile < ApplicationRecord
  belongs_to :target

  enum :kind, { sub: 0, stacked: 1, preview: 2, log: 3 }

  validates :url, presence: true

  scope :recent_first, -> { order(captured_at: :desc, created_at: :desc) }
end
