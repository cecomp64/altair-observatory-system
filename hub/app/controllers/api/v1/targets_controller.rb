module Api
  module V1
    class TargetsController < BaseController
      before_action :set_target

      # PATCH /api/v1/targets/:id/progress
      # Body: { exposure_plans: [{ id, completed_count }], status? }
      def progress
        ActiveRecord::Base.transaction do
          Array(params[:exposure_plans]).each do |plan_params|
            plan = @target.exposure_plans.find(plan_params[:id])
            plan.update!(completed_count: plan_params[:completed_count])
          end

          if params[:status].present?
            @target.update!(status: params[:status])
          elsif @target.fully_captured? && !@target.completed?
            @target.update!(status: :completed)
          end
        end

        @target.target_events.create!(event_type: :progress, payload: {
          exposure_plans: @target.exposure_plans.map { |p| { id: p.id, completed_count: p.completed_count } },
          status: @target.status
        })

        render json: { ok: true, target: { id: @target.id, status: @target.status, percent_complete: @target.percent_complete } }
      rescue ActiveRecord::RecordInvalid, ActiveRecord::RecordNotFound => e
        render json: { error: e.message }, status: :unprocessable_content
      end

      # POST /api/v1/targets/:id/files
      # Body: { url, kind, filter?, captured_at? }
      def files
        file = @target.target_files.create!(
          url: params[:url],
          kind: params[:kind] || "sub",
          filter: params[:filter],
          captured_at: params[:captured_at]
        )

        @target.update!(preview_image_url: file.url) if file.preview?

        @target.target_events.create!(event_type: :file_added, payload: { kind: file.kind, url: file.url })

        render json: { ok: true, file: { id: file.id, url: file.url, kind: file.kind } }, status: :created
      rescue ActiveRecord::RecordInvalid => e
        render json: { error: e.message }, status: :unprocessable_content
      end

      # POST /api/v1/targets/:id/events
      # Body: { event_type, payload }
      def events
        event = @target.target_events.create!(event_type: params[:event_type], payload: params[:payload] || {})
        render json: { ok: true, event: { id: event.id, event_type: event.event_type } }, status: :created
      rescue ActiveRecord::RecordInvalid => e
        render json: { error: e.message }, status: :unprocessable_content
      end

      private

      def set_target
        @target = Target.find(params[:id])
        return if @target.telescope_id == current_api_key.telescope_id

        render json: { error: "This API key is not authorized for that target" }, status: :forbidden
      rescue ActiveRecord::RecordNotFound
        render json: { error: "Target not found" }, status: :not_found
      end
    end
  end
end
