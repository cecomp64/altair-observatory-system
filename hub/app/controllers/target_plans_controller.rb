# Change what a target should capture: more (or fewer) frames per plan, a new
# filter or exposure, or drop a plan nothing has been captured for. The rig
# agent updates Target Scheduler at its next sync.
class TargetPlansController < ApplicationController
  before_action :set_target

  def edit
    @plans = @target.exposure_plans.order(:id)
    @train = @target.effective_optical_train
  end

  def update
    @plans = @target.exposure_plans.order(:id).to_a
    @train = @target.effective_optical_train
    errors = []
    ExposurePlan.transaction do
      @plans.each do |plan|
        attrs = plan_params.dig(:plans, plan.id.to_s) || {}
        if attrs[:remove] == "1"
          next errors << "#{plan}: frames were already captured, so it can't be removed" unless plan.removable?

          plan.destroy!
        elsif attrs[:desired_count].present? && attrs[:desired_count].to_i != plan.desired_count
          plan.update(desired_count: attrs[:desired_count].to_i) or errors.concat(plan.errors.full_messages.map { |m| "#{plan.filter}: #{m}" })
        end
      end
      add = plan_params[:new_plan] || {}
      if add.values.any?(&:present?)
        plan = @target.exposure_plans.build(add.slice(:filter, :exposure_seconds, :desired_count))
        plan.save or errors.concat(plan.errors.full_messages.map { |m| "New plan: #{m}" })
      end
      raise ActiveRecord::Rollback if errors.any?
    end

    if errors.any?
      @plans = @target.exposure_plans.reload.order(:id)
      flash.now[:alert] = errors.to_sentence
      return render :edit, status: :unprocessable_content
    end

    if @target.exposure_plans.reload.empty?
      return redirect_to(edit_target_plans_path(@target), alert: "A target needs at least one exposure plan.")
    end

    @target.settle_status!(by: current_user)
    @target.target_events.create!(event_type: :status_changed,
                                  payload: { status: "plans_updated", by: current_user.display_name,
                                             plans: @target.exposure_plans.map(&:to_s) })
    redirect_to @target, notice: "Exposure plans saved. The telescope picks them up at its next sync."
  end

  private

  def set_target
    @target = Target.find(params[:target_id])
    authorize @target, :manage?
    redirect_to(@target, alert: "Draft and cancelled targets can't be changed here.") if @target.draft? || @target.cancelled?
  end

  # plans: { "<id>" => { desired_count, remove } }; only those two are read.
  def plan_params
    params.fetch(:target, {}).permit(plans: {}, new_plan: [ :filter, :exposure_seconds, :desired_count ])
  end
end
