module Api
  module V1
    class BaseController < ActionController::API
      before_action :authenticate_api_key!

      attr_reader :current_api_key

      private

      def authenticate_api_key!
        token = request.headers["Authorization"]&.sub(/\ABearer\s+/i, "") || request.headers["X-Api-Key"]
        @current_api_key = ApiKey.authenticate(token)

        if @current_api_key.nil?
          render json: { error: "Invalid or missing API key" }, status: :unauthorized
          return
        end

        @current_api_key.touch_last_used!
      end

      # Ensures the authenticated key is scoped to the telescope being acted on.
      def authorize_telescope!(telescope)
        return if current_api_key.telescope_id == telescope.id

        render json: { error: "This API key is not authorized for that telescope" }, status: :forbidden
      end
    end
  end
end
