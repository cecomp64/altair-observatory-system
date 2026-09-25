class EquipmentEvent < ApplicationRecord
  KINDS = %w[sensor_cleaned filter_changed camera_rotated_manually reducer_changed collimated other].freeze

  belongs_to :optical_train
  belongs_to :created_by, class_name: "User", optional: true

  validates :kind, inclusion: { in: KINDS }
  validates :at, presence: true
end
