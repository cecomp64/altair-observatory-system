class TargetEvent < ApplicationRecord
  belongs_to :target

  enum :event_type, {
    progress: 0, file_added: 1, status_changed: 2, error: 3,
    frames_collected: 4, night_closed: 5, master_updated: 6, issue_opened: 7, issue_resolved: 8, session: 9
  }

  after_create_commit :notify_owner

  scope :recent_first, -> { order(created_at: :desc) }

  def summary
    case event_type
    when "progress"
      "Progress updated"
    when "file_added"
      "New file: #{payload['kind']}"
    when "status_changed"
      "Status changed to #{payload['status']}"
    when "error"
      "Error: #{payload['message']}"
    when "frames_collected"
      "#{payload['count']} #{payload['filter']} frame#{'s' unless payload['count'].to_i == 1} collected (night of #{payload['night']})"
    when "night_closed"
      "Night of #{payload['night']} closed with #{payload['lights']} lights"
    when "master_updated"
      "New #{payload['kind'].to_s.humanize.downcase} for #{payload['filter']}#{" (#{payload['frames']} frames)" if payload['frames']}"
    when "issue_opened"
      "Issue: #{payload['kind']} — #{payload['message']}"
    when "issue_resolved"
      "Resolved: #{payload['kind']} (#{payload['status']})"
    when "session"
      "#{payload['event'].to_s.humanize} at #{payload['at']}"
    else
      event_type
    end
  end

  private

  def notify_owner
    NotifyOwnerJob.perform_later(id)
  end
end
