module Api
  module V1
    module Processing
      class NightsController < BaseController
        require_scope "frames:write", only: :update
        require_scope "targets:read", only: :digest
        before_action :set_train_and_night

        # PUT /api/v1/processing/nights/:optical_train/:night
        def update
          body = json_body
          record = ObservingNight.find_or_initialize_by(optical_train: @train, night: @night)
          was_closed = record.state == "closed"
          record.assign_attributes(
            telescope: @train.telescope, state: body["state"], closed_by: body["closed_by"],
            session_end_at: body["session_end_at"] || record.session_end_at,
            lights_count: body["lights_count"] || record.lights_count,
            calibration_count: body["calibration_count"] || record.calibration_count,
            light_seconds: body["light_seconds"] || record.light_seconds,
            manifest_sha256: body["manifest_sha256"] || record.manifest_sha256
          )
          record.save!
          announce_closed(record) if record.state == "closed" && !was_closed
          render json: { ok: true, night: { optical_train: @train.key, night: @night.iso8601, state: record.state } }
        rescue ActiveRecord::RecordInvalid => e
          render_error(e.message, status: :unprocessable_content)
        end

        # GET /api/v1/processing/nights/:optical_train/:night/digest
        def digest
          rows = Frame.where(optical_train: @train, night: @night).pluck(:sha256, :image_type)
          xor = rows.reduce("\0" * 32) { |acc, (sha, _)| acc.bytes.zip([ sha ].pack("H*").bytes).map { |a, b| a ^ b }.pack("C*") }
          render json: { frame_count: rows.size, sha256_xor: xor.unpack1("H*"), by_type: rows.map(&:last).tally }
        end

        private

        def set_train_and_night
          return if performed?

          @train = optical_train_for!(params[:optical_train])
          return if performed?

          @night = Date.iso8601(params[:night])
        rescue Date::Error
          render_error("night must be YYYY-MM-DD", status: :unprocessable_content)
        end

        # One night_closed event per target that has lights that night.
        def announce_closed(record)
          counts = Frame.lights.where(optical_train: @train, night: @night).where.not(target_id: nil).group(:target_id).count
          Target.where(id: counts.keys).find_each do |target|
            target.target_events.create!(event_type: :night_closed, payload: { night: @night.iso8601, lights: counts[target.id], closed_by: record.closed_by })
          end
        end
      end
    end
  end
end
