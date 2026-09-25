# Recomputes plan counters: for the given targets (debounced after frame and
# product reports), or for every active target (daily, so counters can never
# drift from frames and data products).
class ProgressRecomputeJob < ApplicationJob
  queue_as :default

  DEBOUNCE = 30.seconds

  # Queue a recompute for these targets unless one is already queued.
  def self.debounce(target_ids)
    ids = target_ids.compact.uniq.reject { |id| Rails.cache.read("progress-recompute/#{id}") }
    return if ids.empty?

    ids.each { |id| Rails.cache.write("progress-recompute/#{id}", true, expires_in: DEBOUNCE) }
    set(wait: DEBOUNCE).perform_later(ids)
  end

  def perform(target_ids = nil)
    ids = target_ids || Target.where(status: Target::SCHEDULABLE_STATUSES + %w[completed]).pluck(:id)
    ids.each { |id| Rails.cache.delete("progress-recompute/#{id}") } if target_ids
    Progress::Recompute.targets(ids)
    Project.where(id: Target.where(id: ids).select(:project_id)).find_each(&:broadcast_refresh_later)
  end
end
