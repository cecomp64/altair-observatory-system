module Api
  module V1
    module Processing
      # PUT /api/v1/processing/data_products/:kind/:altair_id — multipart:
      # a `metadata` JSON part, optional `preview` and `thumbnail` JPEGs.
      class DataProductsController < BaseController
        MAX_IMAGE = 10.megabytes

        require_scope "products:write"

        def update
          kind = params[:kind].to_s
          return render_error("unknown kind #{kind.inspect}", status: :unprocessable_content) unless DataProduct::ALTAIR_KINDS.include?(kind)

          meta = metadata
          return render_error("metadata must be a JSON object", status: :unprocessable_content) unless meta.is_a?(Hash)

          target = target_for!(meta["target_id"])
          return if performed?
          return render_error("target_id is required", status: :unprocessable_content) unless target

          images = { preview: params[:preview], thumbnail: params[:thumbnail] }.compact
          bad = images.find { |_, file| !jpeg?(file) }
          return render_error("#{bad.first} must be a JPEG up to 10 MB", status: :unprocessable_content) if bad

          product = DataProduct.find_or_initialize_by(processing_node: node, kind: kind, altair_id: params[:altair_id])
          product.assign_attributes(
            target: target, project: target.project, optical_train: target.effective_optical_train,
            night: meta["night"], version: meta["version"], filter: meta["filter"], sha256: meta["sha256"],
            size_bytes: meta["size_bytes"], archive_uri: meta["archive_uri"], nas_path: meta["nas_path"],
            metrics: meta["metrics"] || {}, captured_at: meta["night"] && Date.parse(meta["night"].to_s).in_time_zone
          )
          DataProduct.transaction do
            product.save!
            images.each { |name, file| product.public_send(name).attach(file) }
            supersede(product, meta["supersedes_altair_id"])
          end
          target.target_events.create!(event_type: :master_updated, payload: { kind: kind, filter: product.filter, night: product.night&.iso8601,
                                                                               version: product.version, frames: product.metrics["frames"] })
          ProgressRecomputeJob.debounce([ target.id ])
          render json: { ok: true, id: product.id }
        rescue ActiveRecord::RecordInvalid, ArgumentError => e
          render_error(e.message, status: :unprocessable_content)
        end

        private

        def metadata
          raw = params[:metadata]
          raw = raw.read if raw.respond_to?(:read)
          raw.is_a?(String) ? JSON.parse(raw) : raw&.to_unsafe_h
        rescue JSON::ParserError
          nil
        end

        def jpeg?(file)
          file.respond_to?(:read) && file.size <= MAX_IMAGE && file.read(3)&.b == "\xFF\xD8\xFF".b
        ensure
          file.rewind if file.respond_to?(:rewind)
        end

        def supersede(product, altair_id)
          return if altair_id.blank?

          DataProduct.where(processing_node: node, kind: product.kind, altair_id: altair_id).where.not(id: product.id)
                     .update_all(superseded_by_id: product.id)
        end
      end
    end
  end
end
