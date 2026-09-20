module Api
  module V1
    class TelescopesController < BaseController
      # GET /api/v1/telescopes/:id/active_targets
      #
      # Returns every target the worker should have scheduled in NINA
      # Target Scheduler for this telescope right now (submitted, active,
      # or in_progress), with its exposure plans and remaining counts.
      def active_targets
        telescope = Telescope.find_by(slug: params[:id]) || Telescope.find_by(id: params[:id])
        return render json: { error: "Telescope not found" }, status: :not_found unless telescope
        return if performed? # authorize_telescope! already rendered

        authorize_telescope!(telescope)
        return if performed?

        targets = telescope.targets.schedulable.includes(:exposure_plans).order(priority: :desc, submitted_at: :asc)

        render json: {
          telescope: { id: telescope.id, slug: telescope.slug, name: telescope.name },
          targets: targets.map { |target| target_json(target) }
        }
      end

      private

      def target_json(target)
        {
          id: target.id,
          name: target.name,
          ra_deg: target.ra_deg.to_f,
          dec_deg: target.dec_deg.to_f,
          status: target.status,
          priority: target.priority,
          notes: target.notes,
          exposure_plans: target.exposure_plans.map do |plan|
            {
              id: plan.id,
              filter: plan.filter,
              exposure_seconds: plan.exposure_seconds,
              desired_count: plan.desired_count,
              completed_count: plan.completed_count,
              remaining_count: [ plan.desired_count - plan.completed_count, 0 ].max
            }
          end
        }
      end
    end
  end
end
