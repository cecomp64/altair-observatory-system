module Api
  module V1
    # POST /api/v1/heartbeat — from workers (telescope keys) and Altair (node keys).
    class HeartbeatsController < BaseController
      require_scope "heartbeat:write"

      def create
        status = { "agent" => params[:agent], "version" => params[:version], "api_revision" => params[:api_revision],
                   "status" => params[:status].respond_to?(:to_unsafe_h) ? params[:status].to_unsafe_h : {} }
        if current_processing_node
          current_processing_node.update!(last_heartbeat_at: Time.current, status: status)
        else
          current_telescope.update_columns(worker_last_heartbeat_at: Time.current, worker_status: status)
        end
        render json: { ok: true, api_revision: ::Processing::ConfigBuilder::API_REVISION }
      end
    end
  end
end
