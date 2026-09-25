class ExposurePlan < ApplicationRecord
  # A light counts toward a plan when its exposure is within this of the plan's (§4.3).
  EXPOSURE_MATCH_TOLERANCE_S = 0.5

  belongs_to :target

  validates :filter, presence: true
  validates :exposure_seconds, numericality: { greater_than: 0 }
  validates :desired_count, numericality: { greater_than: 0 }
  validates :completed_count, :collected_count, :usable_count, :integrated_count,
    numericality: { greater_than_or_equal_to: 0 }
  validate :filter_is_canonical_for_optical_train

  def percent_complete
    return 0 if desired_count.zero?

    ((completed_count.to_f / desired_count) * 100).round(1)
  end

  def remaining_count
    [ desired_count - completed_count, 0 ].max
  end

  # What the worker writes into Target Scheduler as the desired count (§4.3).
  # With basis "integrated", frames Target Scheduler accepted but Altair
  # rejected are imaged again.
  def schedule_count
    return desired_count unless target.project&.completion_integrated?

    desired_count + [ completed_count - usable_count, 0 ].max
  end

  def total_exposure_seconds
    exposure_seconds * desired_count
  end

  def to_s
    "#{filter} #{exposure_seconds}s x#{desired_count}"
  end

  private

  # Only enforced when the optical train has a filter list, and only when the
  # filter changes, so plans created before filter lists existed stay valid.
  def filter_is_canonical_for_optical_train
    return unless new_record? || will_save_change_to_filter?

    train = target&.effective_optical_train
    return if train.nil? || train.filter_names.empty?

    canonical = train.canonical_filter(filter)
    if canonical
      self.filter = canonical
    else
      errors.add(:filter, "must be one of #{train.filter_names.to_sentence(last_word_connector: ' or ')}")
    end
  end
end
