module Api
  module V1
    module Processing
      # Endpoints for Altair processing nodes (§5.3). Only node keys may call
      # them, and only for the telescopes the node serves.
      class BaseController < Api::V1::BaseController
        before_action :require_processing_node!

        private

        def require_processing_node!
          return if performed? || current_processing_node

          render_error("Only processing node keys may use this endpoint", status: :forbidden)
        end

        def node
          current_processing_node
        end

        def optical_train_for!(key)
          train = OpticalTrain.joins(:telescope).where(telescope_id: node.telescope_ids).find_by(key: key)
          render_error("Optical train #{key.inspect} is not served by this node", status: :not_found) unless train
          train
        end

        def target_for!(id)
          target = Target.where(telescope_id: node.telescope_ids).find_by(id: id)
          render_error("Target #{id.inspect} is not on a telescope this node serves", status: :not_found) if id.present? && target.nil?
          target
        end

        def json_body
          @json_body ||= request.request_parameters.presence || (JSON.parse(request.raw_post) rescue {})
        end
      end
    end
  end
end
