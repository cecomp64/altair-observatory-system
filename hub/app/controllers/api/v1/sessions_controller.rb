module Api
  module V1
    # POST /api/v1/telescopes/:telescope_id/sessions (§5.2): roof and session
    # events from the worker. session_end queues night_ready for Altair.
    class SessionsController < BaseController
      EVENTS = %w[roof_open roof_close session_end].freeze

      require_scope "sessions:write"

      def create
        telescope = Telescope.find_by(slug: params[:telescope_id])
        return render_error("Telescope not found", status: :not_found) unless telescope

        authorize_telescope!(telescope)
        return if performed?

        event = params[:event].to_s
        return render_error("event must be one of #{EVENTS.join(', ')}", status: :unprocessable_content) unless EVENTS.include?(event)

        at = Time.zone.parse(params[:at].to_s) || Time.current
        night = params[:night].present? ? Date.iso8601(params[:night]) : telescope.night_for(at)
        trains = telescope.optical_trains.active.to_a
        commands = []
        ActiveRecord::Base.transaction do
          trains.each do |train|
            record = ObservingNight.find_or_initialize_by(optical_train: train, night: night)
            record.telescope = telescope
            case event
            when "roof_open" then record.roof_open_at = at
            when "roof_close" then record.roof_closed_at = at
            when "session_end" then record.session_end_at = at
            end
            record.save!
            next unless event == "session_end"

            commands += ::Processing::CommandIssuer.issue!(
              kind: "night_ready", telescope: telescope,
              payload: { optical_train: train.key, night: night.iso8601, at: at.utc.iso8601, closed_by: "session_end" }
            )
          end
          Target.where(telescope: telescope, id: Array(params[:target_ids])).find_each do |target|
            target.target_events.create!(event_type: :session, payload: { event: event, at: at.utc.iso8601, night: night.iso8601 })
          end
        end
        render json: { ok: true, night: night.iso8601, commands_queued: commands.size }, status: :created
      rescue Date::Error
        render_error("night must be YYYY-MM-DD", status: :unprocessable_content)
      end
    end
  end
end
