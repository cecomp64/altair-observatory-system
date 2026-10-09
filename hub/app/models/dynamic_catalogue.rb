# A list of objects that changes on its own and is refreshed on a schedule
# (DynamicCatalogueRefreshJob). What a list is and how it is fetched lives in
# Catalogue::Dynamic::SOURCES; this row holds its membership and the state of
# its last refresh.
class DynamicCatalogue < ApplicationRecord
  has_many :entries, class_name: "DynamicCatalogueEntry", dependent: :delete_all, inverse_of: :dynamic_catalogue
  has_many :active_entries, -> { active }, class_name: "DynamicCatalogueEntry", inverse_of: :dynamic_catalogue

  validates :key, presence: true, uniqueness: true, inclusion: { in: ->(_) { Catalogue::Dynamic::SOURCES.keys } }

  def self.for(key)
    find_or_create_by!(key: key)
  end

  # Every known list, in registry order, including ones never refreshed.
  def self.all_sources
    rows = where(key: Catalogue::Dynamic::SOURCES.keys).index_by(&:key)
    Catalogue::Dynamic::SOURCES.keys.map { |key| rows[key] || new(key: key) }
  end

  def source = Catalogue::Dynamic::SOURCES.fetch(key)
  def name = source::NAME
  def short_name = source::SHORT_NAME
  def configured? = source.configured?
  def to_param = key
end
