# /telescopes/:telescope_id/optical_trains/:id — optics, filters, equipment
# log, calibration library and the flats shopping list from open
# FLAT_MISSING issues (§7.3).
class OpticalTrainsController < ApplicationController
  def show
    @telescope = policy_scope(Telescope).find_by!(slug: params[:telescope_id])
    authorize @telescope, :show?
    @train = @telescope.optical_trains.find_by!(key: params[:id])
    @events = @train.equipment_events.order(at: :desc).limit(50).includes(:created_by)
    @masters = @train.calibration_masters.current.order(:kind, :filter, :night)
    @flats_needed = ProcessingIssue.open.where(optical_train: @train, kind: "FLAT_MISSING").order(:filter, :night)
  end
end
