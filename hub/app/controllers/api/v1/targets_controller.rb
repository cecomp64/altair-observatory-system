module Api
  module V1
    class TargetsController < BaseController
      require_scope "progress:write", only: :progress
      require_scope "events:write", only: :events
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

      # POST /api/v1/targets/:id/events
      # Body: { event_type, payload }
      def events
        event = @target.target_events.create!(event_type: params[:event_type], payload: params[:payload] || {})
        render json: { ok: true, event: { id: event.id, event_type: event.event_type } }, status: :created
      rescue ActiveRecord::RecordInvalid, ArgumentError => e
        render json: { error: e.message }, status: :unprocessable_content
      end

      private

      def set_target
        return if performed?

        @target = Target.find(params[:id])
        return if current_api_key.telescope_ids.include?(@target.telescope_id)

        render json: { error: "This API key is not authorized for that target" }, status: :forbidden
      rescue ActiveRecord::RecordNotFound
        render json: { error: "Target not found" }, status: :not_found
      end
    end
  end
end
