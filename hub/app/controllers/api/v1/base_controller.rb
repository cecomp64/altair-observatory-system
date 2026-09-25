module Api
  module V1
    # Authenticates an API key and exposes its principal: a Telescope (worker)
    # or a ProcessingNode (Altair). Actions declare the scope they need with
    # `require_scope!` and check resource access with `authorize_telescope!`.
    class BaseController < ActionController::API
      before_action :authenticate_api_key!

      rate_limit to: 600, within: 1.minute, by: -> { request.headers["Authorization"].to_s + request.headers["X-Api-Key"].to_s },
        with: -> { render json: { error: "Rate limit exceeded" }, status: :too_many_requests }

      attr_reader :current_api_key

      def self.require_scope(scope, **options)
        before_action(**options) { require_scope!(scope) }
      end

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

      def require_scope!(scope)
        return if performed? || current_api_key.allows?(scope)

        render json: { error: "This API key lacks the #{scope} scope" }, status: :forbidden
      end

      def current_telescope
        current_api_key.telescope
      end

      def current_processing_node
        current_api_key.processing_node
      end

      # Ensures the authenticated key may act on the telescope: its own
      # telescope for a worker key, or a telescope its node serves.
      def authorize_telescope!(telescope)
        return if current_api_key.may_access_telescope?(telescope)

        render json: { error: "This API key is not authorized for that telescope" }, status: :forbidden
      end

      def render_error(message, status:, details: nil)
        body = { error: message }
        body[:details] = details if details
        render json: body, status: status
      end
    end
  end
end
