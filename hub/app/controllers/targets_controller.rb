class TargetsController < ApplicationController
  before_action :set_target, only: [ :show, :cancel, :pause, :resume, :reopen ]

  def index
    @targets = policy_scope(Target).includes(:telescope, :exposure_plans, :project).order(created_at: :desc)
  end

  def show
    authorize @target
    @exposure_plans = @target.exposure_plans.order(:id)
    @masters = @target.data_products.masters.current.recent_first
    @masters_zips = @target.data_products.masters_bundle.current.order(:id)
                      .includes(preview_attachment: :blob, thumbnail_attachment: :blob)
    @preview = @masters.find { |m| m.multi_night_master? && m.preview.attached? } || @masters.find { |m| m.preview.attached? }
    @events = @target.target_events.recent_first.limit(20)
  end

  def cancel
    authorize @target
    @target.update!(status: :cancelled, paused_at: nil)
    @target.target_events.create!(event_type: :status_changed, payload: { status: "cancelled", by: current_user.display_name })
    redirect_to @target, notice: "Target cancelled."
  end

  # The rig agent drops a paused target from Target Scheduler at its next
  # sync and brings it back, with its accepted counts, on resume.
  def pause
    authorize @target, :manage?
    change(@target.pause!(by: current_user), "Target paused. The telescope stops imaging it at its next sync.",
           "Only an open target can be paused.")
  end

  def resume
    authorize @target, :manage?
    return if refuse_without_membership
    change(@target.resume!(by: current_user), "Target resumed.", "The target isn't paused.")
  end

  def reopen
    authorize @target, :manage?
    return if refuse_without_membership
    change(@target.reopen!(by: current_user), "Target reopened.",
           "Everything is already captured: add frames to its exposure plans to reopen it.")
  end

  private

  def change(ok, notice, alert)
    ok ? redirect_to(@target, notice: notice) : redirect_to(@target, alert: alert)
  end

  # Work on a members-only telescope only restarts for a current member.
  def refuse_without_membership
    return false if policy(@target.telescope).use?

    redirect_to @target, alert: "#{@target.telescope.name} needs a current SJAA membership. Link or refresh it on your profile."
  end

  def set_target
    @target = Target.find(params[:id])
  end
end
