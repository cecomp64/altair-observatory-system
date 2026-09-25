module Api
  module V1
    module Processing
      class CommandsController < BaseController
        require_scope "commands:read"

        # GET /api/v1/processing/commands?state=pending — marks them delivered.
        def index
          commands = node.processing_commands.where(state: params[:state].presence || "pending").order(:id).limit(200).to_a
          node.processing_commands.where(id: commands.map(&:id), state: "pending").update_all(state: "delivered", delivered_at: Time.current)
          render json: commands.map(&:as_api_json)
        end

        # POST /api/v1/processing/commands/:id/ack { state, result }
        def ack
          command = node.processing_commands.find_by(id: params[:id])
          return render_error("Command not found", status: :not_found) unless command

          state = json_body["state"]
          return render_error("state must be succeeded or failed", status: :unprocessable_content) unless %w[succeeded failed].include?(state)

          command.update!(state: state, result: json_body["result"], completed_at: Time.current, delivered_at: command.delivered_at || Time.current)
          render json: { ok: true }
        end
      end
    end
  end
end
