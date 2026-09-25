module Admin
  # Logging an equipment event (sensor cleaned, filter changed, ...) on an
  # observatory optical train; Altair applies it to calibration matching.
  class EquipmentEventsController < BaseController
    def create
      telescope = Telescope.find_by_param!(params[:telescope_id])
      train = telescope.optical_trains.find_by!(key: params[:optical_train_id])
      event = train.equipment_events.create!(
        kind: params[:kind], filter: params[:filter].presence, note: params[:note].presence,
        at: (Time.zone.parse(params[:at].to_s) if params[:at].present?) || Time.current, created_by: current_user
      )
      Processing::CommandIssuer.issue!(kind: "equipment_event", telescope: telescope, payload: { equipment_event_id: event.id }, requested_by: current_user)
      redirect_to telescope_optical_train_path(telescope, train), notice: "Logged #{event.kind.humanize.downcase}."
    rescue ActiveRecord::RecordInvalid => e
      redirect_to telescope_optical_train_path(telescope, train), alert: e.message
    end
  end
end
