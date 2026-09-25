module Api
  module V1
    module Processing
      class ConfigController < BaseController
        require_scope "targets:read"

        # GET /api/v1/processing/config (ETag; If-None-Match -> 304)
        def show
          builder = ::Processing::ConfigBuilder.new(node)
          etag = %("#{builder.etag}")
          response.headers["ETag"] = etag
          return head(:not_modified) if request.headers["If-None-Match"].to_s.split(/\s*,\s*/).include?(etag)

          render json: builder.payload
        end
      end
    end
  end
end
