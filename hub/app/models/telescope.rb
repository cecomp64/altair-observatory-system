class Telescope < ApplicationRecord
  has_many :targets, dependent: :destroy
  has_many :api_keys, dependent: :destroy
  has_one_attached :horizon_file

  before_validation :generate_slug, on: :create

  validates :name, presence: true
  validates :slug, presence: true, uniqueness: true, format: { with: /\A[a-z0-9\-]+\z/ }
  validates :latitude, presence: true, numericality: { greater_than_or_equal_to: -90, less_than_or_equal_to: 90 }
  validates :longitude, presence: true, numericality: { greater_than_or_equal_to: -180, less_than_or_equal_to: 180 }

  scope :active, -> { where(active: true) }

  def to_param
    slug
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
end
