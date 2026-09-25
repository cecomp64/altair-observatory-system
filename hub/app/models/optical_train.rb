# Telescope + camera + reducer as used for imaging. One optical train is one
# Altair rig: `key` equals the rig name in altair.yaml.
class OpticalTrain < ApplicationRecord
  belongs_to :telescope
  has_many :targets, dependent: :nullify
  has_many :frames, dependent: :restrict_with_error
  has_many :observing_nights, dependent: :delete_all
  has_many :calibration_masters, dependent: :delete_all
  has_many :equipment_events, dependent: :delete_all

  enum :camera_type, { mono: "mono", osc: "osc" }, validate: true

  validates :key, presence: true, uniqueness: { scope: :telescope_id }, format: { with: /\A[a-z0-9_\-]+\z/ }
  validates :name, presence: true
  validates :focal_length_mm, :pixel_size_um, numericality: { greater_than: 0 }, allow_nil: true
  validates :sensor_width_px, :sensor_height_px, numericality: { greater_than: 0, only_integer: true }, allow_nil: true
  validate :filters_are_well_formed

  scope :active, -> { where(active: true) }

  def to_param
    key
  end

  # [{ "name" => "Ha", "aliases" => ["H-alpha"], "bandpass_nm" => 7 }, ...]
  def filter_list
    Array(filters).map { |f| f.stringify_keys }
  end

  def filter_names
    filter_list.map { |f| f["name"] }
  end

  # Maps a raw FILTER header value onto a canonical filter name:
  # case-insensitive exact match on names, then on aliases. nil if unknown.
  def canonical_filter(raw)
    return nil if raw.blank?

    needle = raw.to_s.strip.downcase
    filter_list.each { |f| return f["name"] if f["name"].to_s.downcase == needle }
    filter_list.each { |f| return f["name"] if Array(f["aliases"]).any? { |a| a.to_s.downcase == needle } }
    nil
  end

  # Edited as one line per filter in the admin form: "Ha: H-alpha, HA".
  def filters_text
    filter_list.map { |f| [ f["name"], Array(f["aliases"]).join(", ") ].reject(&:blank?).join(": ") }.join("\n")
  end

  def filters_text=(text)
    self.filters = text.to_s.lines.filter_map do |line|
      name, aliases = line.strip.split(":", 2)
      next if name.blank?

      { "name" => name.strip, "aliases" => aliases.to_s.split(",").map(&:strip).reject(&:blank?) }
    end
  end

  # Altair checks its rig against these (§8.2.2); a train without them isn't
  # sent to processing nodes.
  def complete_optics?
    focal_length_mm.to_f.positive? && pixel_size_um.to_f.positive? && sensor_width_px.to_i.positive? && sensor_height_px.to_i.positive?
  end

  def pixel_scale_arcsec
    return nil unless pixel_size_um && focal_length_mm

    206.265 * pixel_size_um.to_f / focal_length_mm.to_f
  end

  # [width_deg, height_deg] of the sensor, or nil when the optics are unknown.
  def fov_deg
    scale = pixel_scale_arcsec
    return nil unless scale && sensor_width_px && sensor_height_px

    [ sensor_width_px * scale / 3600.0, sensor_height_px * scale / 3600.0 ]
  end

  def fov_diagonal_deg
    w, h = fov_deg
    w && Math.hypot(w, h)
  end

  private

  def filters_are_well_formed
    list = filters
    unless list.is_a?(Array) && list.all? { |f| f.is_a?(Hash) && f.stringify_keys["name"].present? }
      return errors.add(:filters, "must be a list of { name, aliases }")
    end

    names = filter_names.map(&:downcase)
    errors.add(:filters, "have duplicate names") if names.uniq.size != names.size
  end
end
