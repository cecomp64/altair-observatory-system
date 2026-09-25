# A catalogue object (port of astrophotography-database `objects`).
class AstroObject < ApplicationRecord
  SOURCES = %w[openngc ldn lbn telescopius custom].freeze

  has_many :aliases, class_name: "ObjectAlias", dependent: :destroy, inverse_of: :astro_object
  has_one :showcase, class_name: "ObjectShowcase", dependent: :destroy
  has_many :targets, dependent: :nullify
  belongs_to :created_by, class_name: "User", optional: true

  validates :primary_name, presence: true
  validates :source, inclusion: { in: SOURCES }
  validates :ra_deg, numericality: { greater_than_or_equal_to: 0, less_than: 360 }, allow_nil: true
  validates :dec_deg, numericality: { greater_than_or_equal_to: -90, less_than_or_equal_to: 90 }, allow_nil: true

  after_create :add_primary_alias

  # Fuzzy search over primary names and aliases (pg_trgm). Exact alias hits
  # rank first, then prefix hits, then trigram similarity.
  scope :search, lambda { |query|
    normalized = Catalogue::AliasNormalizer.normalize(query)
    next none if normalized.blank?

    like = "%#{sanitize_sql_like(normalized)}%"
    matching = ObjectAlias.where("object_aliases.normalized_name LIKE ?", like)
                          .or(ObjectAlias.where("object_aliases.normalized_name % ?", normalized))
                          .select(:astro_object_id)
    rank = sanitize_sql_array([ <<~SQL.squish, normalized, "#{normalized}%", normalized ])
      (SELECT MIN(CASE WHEN oa.normalized_name = ? THEN 0 WHEN oa.normalized_name LIKE ? THEN 1 ELSE 2 END
                      - similarity(oa.normalized_name, ?))
       FROM object_aliases oa WHERE oa.astro_object_id = astro_objects.id)
    SQL
    where(id: matching).order(Arel.sql(rank), :primary_name)
  }

  def self.find_by_alias(name)
    normalized = Catalogue::AliasNormalizer.normalize(name)
    return nil if normalized.blank?

    joins(:aliases).find_by(object_aliases: { normalized_name: normalized })
  end

  def add_alias(name, catalog: nil)
    normalized = Catalogue::AliasNormalizer.normalize(name)
    return if normalized.blank? || aliases.any? { |a| a.normalized_name == normalized }

    aliases.create!(name: name.strip, catalog: catalog || Catalogue::AliasNormalizer.catalog_for(name))
  end

  def alias_names
    aliases.map(&:name)
  end

  def coordinates?
    ra_deg.present? && dec_deg.present?
  end

  def to_s
    primary_name
  end

  private

  def add_primary_alias
    add_alias(primary_name)
  end
end
