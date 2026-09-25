class FrameObject < ApplicationRecord
  belongs_to :frame
  belongs_to :astro_object

  validates :association_type, inclusion: { in: %w[primary in_fov] }
end
