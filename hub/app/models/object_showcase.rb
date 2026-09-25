# The picture shown for a catalogue object: an upload, one of our own data
# products, or a survey cut-out.
class ObjectShowcase < ApplicationRecord
  belongs_to :astro_object
  belongs_to :data_product, optional: true
  has_one_attached :image

  enum :source_type, { upload: "upload", product: "product", survey: "survey" }, validate: true
end
