module Api
  module V1
    module Processing
      class CalibrationMastersController < BaseController
        require_scope "frames:write"

        # PUT /api/v1/processing/calibration_masters/:altair_id
        def update
          body = json_body
          train = optical_train_for!(body["optical_train"])
          return if performed?

          master = node.calibration_masters.find_or_initialize_by(altair_id: params[:altair_id])
          master.update!(optical_train: train, **body.slice(*%w[kind filter exposure_s gain offset binning sensor_temp_c rotator_pos night n_frames sha256 superseded]).symbolize_keys)
          render json: { ok: true, id: master.id }
        rescue ActiveRecord::RecordInvalid, ArgumentError => e
          render_error(e.message, status: :unprocessable_content)
        end
      end
    end
  end
end
