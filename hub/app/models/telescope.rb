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

  validates :name, presence: true
  validates :slug, presence: true, uniqueness: true, format: { with: /\A[a-z0-9\-]+\z/ }
  validates :latitude, presence: true, numericality: { greater_than_or_equal_to: -90, less_than_or_equal_to: 90 }
  validates :longitude, presence: true, numericality: { greater_than_or_equal_to: -180, less_than_or_equal_to: 180 }
  validates :timezone, presence: true
  validate :timezone_is_known
  validates :min_altitude_deg, numericality: { greater_than_or_equal_to: 0, less_than: 90 }

  scope :active, -> { where(active: true) }

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

  # Parses the horizon file, a plain text/CSV file of "azimuth,altitude"
  # pairs (degrees) describing the minimum altitude that is clear of
  # obstructions at each azimuth. Returns an array of [az, alt] floats
  # sorted by azimuth, or [] if no file is attached / it fails to parse.
  def horizon_points
    return [] unless horizon_file.attached?

    horizon_file.download.each_line.filter_map do |line|
      line = line.strip
      next if line.empty? || line.start_with?("#")

      az, alt = line.split(/[,\s]+/).first(2).map(&:to_f)
      [ az, alt ]
    end.sort_by(&:first)
  rescue StandardError
    []
  end

  private

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
