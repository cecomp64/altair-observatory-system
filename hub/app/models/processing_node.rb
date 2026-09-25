# One Altair installation (a processing PC). It serves one or more telescopes.
class ProcessingNode < ApplicationRecord
  has_many :processing_node_telescopes, dependent: :destroy
  has_many :telescopes, through: :processing_node_telescopes
  has_many :api_keys, as: :owner, dependent: :destroy

  validates :name, presence: true, uniqueness: true, format: { with: /\A[a-z0-9_\-]+\z/ }

  scope :active, -> { where(active: true) }

  def to_param
    name
  end
end
