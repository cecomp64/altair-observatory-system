class Telescope < ApplicationRecord
  DEFAULT_TIMEZONE = -> { ENV.fetch("DEFAULT_TELESCOPE_TIMEZONE", "America/Los_Angeles") }

  attribute :timezone, :string, default: DEFAULT_TIMEZONE
  has_many :targets, dependent: :destroy
  has_many :optical_trains, dependent: :destroy
  belongs_to :default_optical_train, class_name: "OpticalTrain", optional: true
  has_many :api_keys, as: :owner, dependent: :destroy
  has_many :processing_node_telescopes, dependent: :destroy
  has_many :processing_nodes, through: :processing_node_telescopes
  has_many :observing_nights, dependent: :delete_all
  has_one_attached :horizon_file

  before_validation :generate_slug, on: :create
  after_create :create_default_optical_train
  validate :horizon_file_parses, if: -> { attachment_changes["horizon_file"].is_a?(ActiveStorage::Attached::Changes::CreateOne) }
  before_save :cache_horizon_points, if: -> { attachment_changes.key?("horizon_file") }

  validates :name, presence: true
  validates :slug, presence: true, uniqueness: true, format: { with: /\A[a-z0-9\-]+\z/ }
  validates :latitude, presence: true, numericality: { greater_than_or_equal_to: -90, less_than_or_equal_to: 90 }
  validates :longitude, presence: true, numericality: { greater_than_or_equal_to: -180, less_than_or_equal_to: 180 }
  validates :timezone, presence: true
  validate :timezone_is_known
  validates :min_altitude_deg, numericality: { greater_than_or_equal_to: 0, less_than: 90 }

  scope :active, -> { where(active: true) }

  # Set by an admin and shown to every member on /observatory.
  OPERATING_STATUSES = %w[operational maintenance offline].freeze
  validates :operating_status, inclusion: { in: OPERATING_STATUSES }
  before_save -> { self.status_changed_at = Time.current }, if: -> { will_save_change_to_operating_status? || will_save_change_to_status_note? }
  after_commit -> { ObservatoryStatus.broadcast }

  def operational?
    operating_status == "operational"
  end

  def to_param
    slug
  end

  # Routes use the slug (to_param); older links and the API also accept the id.
  def self.find_by_param!(param)
    find_by(slug: param) || find(param)
  end

  def time_zone
    ActiveSupport::TimeZone[timezone]
  end

  # The observing night a moment belongs to: the local noon-to-noon window,
  # named by the date it starts (NINA's $$DATEMINUS12$$).
  def night_for(time)
    (time.in_time_zone(time_zone) - 12.hours).to_date
  end

  private

  # horizon_points caches the parsed horizon file (see HorizonFileParser). It
  # is parsed from the attachable, because the blob isn't uploaded until after
  # commit; a file that doesn't parse is rejected.
  def horizon_file_parses
    result = parsed_horizon_file(attachment_changes["horizon_file"])
    errors.add(:horizon_file, result.message) if result.is_a?(HorizonFileParser::Error)
  end

  def cache_horizon_points
    change = attachment_changes["horizon_file"]
    result = parsed_horizon_file(change) if change.is_a?(ActiveStorage::Attached::Changes::CreateOne)
    self.horizon_points = result.is_a?(Array) ? result : []
  end

  # Points, or the HorizonFileParser::Error; parsed once per attachment change.
  def parsed_horizon_file(change)
    (@parsed_horizon_files ||= {}.compare_by_identity)[change] ||= begin
      if change.blob.byte_size > HorizonFileParser::MAX_BYTES
        raise HorizonFileParser::Error, "is larger than #{HorizonFileParser::MAX_BYTES / 1.megabyte} MB"
      end

      HorizonFileParser.parse(read_attachable(change.attachable))
    rescue HorizonFileParser::Error => e
      e
    end
  end

  def read_attachable(attachable)
    case attachable
    when ActiveStorage::Blob then attachable.download
    when String then ActiveStorage::Blob.find_signed!(attachable).download
    when Pathname then attachable.read
    else
      io = attachable.is_a?(Hash) ? attachable.fetch(:io) : attachable
      io.read.tap { io.rewind }
    end
  end

  def generate_slug
    return if name.blank?

    self.slug = name.parameterize if slug.blank?
  end

  def timezone_is_known
    errors.add(:timezone, "is not a known IANA timezone") if timezone.present? && time_zone.nil?
  end

  def create_default_optical_train
    return if default_optical_train_id.present?

    train = optical_trains.first || optical_trains.create!(key: slug, name: "#{name} (default train)")
    update_column(:default_optical_train_id, train.id)
  end
end
