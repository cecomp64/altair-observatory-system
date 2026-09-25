# The Hub's projection of an Altair frame (§4.2). Altair owns the frame's
# identity and headers; the Hub owns manual target assignments.
class Frame < ApplicationRecord
  IMAGE_TYPES = %w[light dark flat bias darkflat].freeze
  STATUSES = %w[collected valid invalid held processed rejected].freeze
  ASSIGNMENT_SOURCES = %w[header_token name coords manual unlinked].freeze
  ORIGINS = %w[collect import legacy_index].freeze

  belongs_to :processing_node, optional: true
  belongs_to :telescope
  belongs_to :optical_train
  belongs_to :target, optional: true
  belongs_to :project, optional: true
  belongs_to :exposure_plan, optional: true
  has_many :frame_objects, dependent: :delete_all
  has_many :astro_objects, through: :frame_objects

  validates :sha256, presence: true, uniqueness: true, format: { with: /\A[0-9a-f]{64}\z/ }
  validates :image_type, inclusion: { in: IMAGE_TYPES }
  validates :status, inclusion: { in: STATUSES }
  validates :assignment_source, inclusion: { in: ASSIGNMENT_SOURCES }
  validates :origin, inclusion: { in: ORIGINS }
  validates :night, :date_obs, :file_name, :logical_path, presence: true

  scope :lights, -> { where(image_type: "light") }
  scope :calibration, -> { where.not(image_type: "light") }
  scope :unassigned, -> { lights.where(target_id: nil) }
  scope :counted, -> { where.not(status: "invalid") }

  # Frames whose centre lies within radius_deg of (ra, dec): a declination
  # band from the index, then an exact great-circle check in SQL.
  scope :cone, lambda { |ra, dec, radius_deg|
    where(dec_deg: (dec - radius_deg)..(dec + radius_deg))
      .where(<<~SQL.squish, ra: ra.to_f, dec: dec.to_f, r: radius_deg.to_f)
        degrees(acos(least(1.0, greatest(-1.0,
          sin(radians(:dec)) * sin(radians(frames.dec_deg)) +
          cos(radians(:dec)) * cos(radians(frames.dec_deg)) * cos(radians(frames.ra_deg - :ra)))))) <= :r
      SQL
  }

  def manual?
    assignment_source == "manual"
  end

  def light?
    image_type == "light"
  end

  def storage_summary
    s = storage || {}
    [ ("NAS" if s["nas"]), (s["s3"] && "S3 #{s['s3']}") ].compact.join(" + ").presence || "—"
  end
end
