module Api
  module V1
    module Processing
      class IssuesController < BaseController
        require_scope "issues:write"

        # PUT /api/v1/processing/issues/:fingerprint
        def update
          body = json_body
          issue = node.processing_issues.find_or_initialize_by(fingerprint: params[:fingerprint])
          previous = issue.new_record? ? nil : issue.status
          train = body["optical_train"].present? ? optical_train_for!(body["optical_train"]) : nil
          return if performed?

          target = target_for!(body["target_id"])
          return if performed?

          issue.assign_attributes(
            altair_id: body["altair_id"], kind: body["kind"], severity: body["severity"], status: body["status"],
            message: body["message"], requirement: body["requirement"], scope: body["scope"] || {},
            optical_train: train || target&.effective_optical_train, telescope: (train || target)&.telescope,
            target: target, project: target&.project, night: body["night"], filter: body["filter"],
            resolution: body["resolution"]
          )
          issue.opened_at = Time.current if issue.new_record? || (issue.status == "open" && previous && previous != "open")
          issue.resolved_at = issue.status == "open" ? nil : (issue.resolved_at || Time.current)
          issue.save!
          IssueNotifier.transition(issue, from: previous)
          render json: { ok: true, id: issue.id }
        rescue ActiveRecord::RecordInvalid, ArgumentError => e
          render_error(e.message, status: :unprocessable_content)
        end
      end
    end
  end
end
