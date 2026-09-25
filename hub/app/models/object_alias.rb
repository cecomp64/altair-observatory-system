class ObjectAlias < ApplicationRecord
  belongs_to :astro_object, inverse_of: :aliases

  before_validation { self.normalized_name = Catalogue::AliasNormalizer.normalize(name) }

  validates :name, :normalized_name, presence: true
  validates :normalized_name, uniqueness: { scope: :astro_object_id }
end
