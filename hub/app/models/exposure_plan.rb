class ExposurePlan < ApplicationRecord
  belongs_to :target

  validates :filter, presence: true
  validates :exposure_seconds, numericality: { greater_than: 0 }
  validates :desired_count, numericality: { greater_than: 0 }
  validates :completed_count, numericality: { greater_than_or_equal_to: 0 }

  def percent_complete
    return 0 if desired_count.zero?

    ((completed_count.to_f / desired_count) * 100).round(1)
  end

  def total_exposure_seconds
    exposure_seconds * desired_count
  end

  def to_s
    "#{filter} #{exposure_seconds}s x#{desired_count}"
  end
end
