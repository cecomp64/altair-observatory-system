class ObservingNight < ApplicationRecord
  belongs_to :telescope
  belongs_to :optical_train

  validates :night, presence: true, uniqueness: { scope: :optical_train_id }
  validates :state, inclusion: { in: %w[open closing closed] }

  def imaging_now?
    roof_open_at.present? && roof_closed_at.nil? && session_end_at.nil?
  end
end
