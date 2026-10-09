# An object on a dynamic catalogue. removed_at is set when it drops off the
# list, and cleared (with a new first_seen_at) if it comes back.
class DynamicCatalogueEntry < ApplicationRecord
  belongs_to :dynamic_catalogue, inverse_of: :entries
  belongs_to :astro_object

  scope :active, -> { where(removed_at: nil) }

  def active? = removed_at.nil?
end
