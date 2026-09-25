module Api
  module V1
    module Processing
      class FramesController < BaseController
        MAX_BATCH = 500
        PATCHABLE = %w[status status_reason quality storage].freeze

        require_scope "frames:write"
        before_action :load_frames

        # POST /api/v1/processing/frames:batch
        def create
          upserter = ::Frames::BatchUpserter.new(node)
          results = ActiveRecord::Base.transaction { upserter.upsert(@items) }
          after_change(upserter.affected_target_ids, fov_frame_ids: upserter.fov_frame_ids)
          render json: { results: results }
        end

        # PATCH /api/v1/processing/frames:batch
        def update
          frames = Frame.where(processing_node: node, sha256: @items.filter_map { |i| i["sha256"] if i.is_a?(Hash) }).index_by(&:sha256)
          affected = Set.new
          results = @items.map do |item|
            frame = item.is_a?(Hash) && frames[item["sha256"]]
            next { sha256: item.is_a?(Hash) ? item["sha256"].to_s : "", status: "error", error: "unknown frame" } unless frame

            changes = item.slice(*PATCHABLE)
            %w[quality storage].each { |k| changes[k] = (frame[k] || {}).merge(changes[k]) if changes[k].is_a?(Hash) }
            frame.update!(changes)
            affected << frame.target_id if frame.target_id
            { sha256: frame.sha256, id: frame.id, target_id: frame.target_id, exposure_plan_id: frame.exposure_plan_id, status: "ok" }
          rescue ActiveRecord::RecordInvalid => e
            { sha256: item["sha256"], status: "error", error: e.message }
          end
          after_change(affected)
          render json: { results: results }
        end

        private

        def load_frames
          return if performed?

          @items = json_body["frames"]
          return render_error("frames must be a non-empty list", status: :unprocessable_content) unless @items.is_a?(Array) && @items.any?

          render_error("at most #{MAX_BATCH} frames per batch", status: :unprocessable_content) if @items.size > MAX_BATCH
        end

        def after_change(target_ids, fov_frame_ids: [])
          ProgressRecomputeJob.debounce(target_ids.to_a)
          FrameFovMatchJob.perform_later(fov_frame_ids) if fov_frame_ids.any?
        end
      end
    end
  end
end
