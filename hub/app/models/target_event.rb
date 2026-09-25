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
    else
      event_type
    end
  end

  private

  def notify_owner
    NotifyOwnerJob.perform_later(id)
  end
end
