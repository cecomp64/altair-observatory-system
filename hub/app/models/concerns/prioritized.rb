# Low, normal or high priority, stored as Target Scheduler's own codes (the
# rig agent writes `priority_code` straight into the Target Scheduler project).
module Prioritized
  extend ActiveSupport::Concern

  PRIORITIES = { low: 0, normal: 1, high: 2 }.freeze

  included do
    enum :priority, PRIORITIES, suffix: true, validate: true
  end

  # The level for a free-form number (imports and the old integer column):
  # below zero is low, zero is normal, above zero is high.
  def self.level_for(number)
    number = number.to_i
    return "normal" if number.zero?

    number.negative? ? "low" : "high"
  end

  def priority_code
    PRIORITIES[priority&.to_sym]
  end
end
