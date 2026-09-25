module Api
  module V1
    class TelescopesController < BaseController
      require_scope "targets:read", only: :active_targets

      # GET /api/v1/telescopes/:id/active_targets
      #
      # Returns every target the worker should have scheduled in NINA
      # Target Scheduler for this telescope right now (submitted, active,
      # or in_progress), with its exposure plans and remaining counts.
      # Fields beyond the original contract (nina_name, project, …) are
      # additive (contracts api_revision 1).
      def active_targets
        telescope = Telescope.find_by(slug: params[:id]) || Telescope.find_by(id: params[:id])
        return render json: { error: "Telescope not found" }, status: :not_found unless telescope

        authorize_telescope!(telescope)
        return if performed?

        targets = telescope.targets.schedulable
                           .includes(:exposure_plans, :project, :optical_train, telescope: :default_optical_train)
                           .order(priority: :desc, submitted_at: :asc)

        render json: {
          telescope: { id: telescope.id, slug: telescope.slug, name: telescope.name, timezone: telescope.timezone },
          targets: targets.map { |target| target_json(target) }
        }
      end

      private

      def target_json(target)
        project = target.project
        train = target.effective_optical_train
        {
          id: target.id,
          name: target.name,
          ra_deg: target.ra_deg.to_f,
          dec_deg: target.dec_deg.to_f,
          status: target.status,
          priority: target.priority,
          notes: target.notes,
          nina_name: target.nina_name,
          rotation_deg: target.rotation_deg&.to_f,
          min_altitude_deg: target.effective_min_altitude_deg,
          project: { id: project.id, name: project.name, priority: project.priority, ts_project_name: project.ts_project_name },
          optical_train: train && { key: train.key },
          exposure_plans: target.exposure_plans.sort_by(&:id).map do |plan|
            {
              id: plan.id,
              filter: plan.filter,
              exposure_seconds: plan.exposure_seconds,
              desired_count: plan.desired_count,
              completed_count: plan.completed_count,
              remaining_count: plan.remaining_count,
              schedule_count: plan.schedule_count
            }
          end
        }
      end
    end
  end
end
