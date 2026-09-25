module Api
  module V1
    module Processing
      class JobsController < BaseController
        require_scope "issues:write"

        # PUT /api/v1/processing/jobs/:altair_id
        def update
          body = json_body
          target = target_for!(body["target_id"])
          return if performed?

          job = node.processing_jobs.find_or_initialize_by(altair_id: params[:altair_id])
          job.update!(target: target, **body.slice(*%w[kind status night filter started_at finished_at error]).symbolize_keys)
          render json: { ok: true, id: job.id }
        rescue ActiveRecord::RecordInvalid, ArgumentError => e
          render_error(e.message, status: :unprocessable_content)
        end
      end
    end
  end
end
