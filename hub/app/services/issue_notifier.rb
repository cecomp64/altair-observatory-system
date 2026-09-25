# Routes processing-issue transitions (§7.4): project-scoped kinds reach the
# target owner (through a target event) and the admins; infrastructure kinds
# reach admins only, plus the system Discord webhook.
class IssueNotifier
  def self.transition(issue, from:)
    new(issue).transition(from)
  end

  def initialize(issue)
    @issue = issue
  end

  def transition(from)
    to = @issue.status
    return if from == to

    if to == "open"
      notify(:opened)
    elsif from == "open"
      notify(:resolved)
    end
  end

  private

  def notify(change)
    if @issue.project_scoped? && @issue.target
      @issue.target.target_events.create!(
        event_type: change == :opened ? :issue_opened : :issue_resolved,
        payload: { issue_id: @issue.id, kind: @issue.kind, severity: @issue.severity, message: @issue.message, status: @issue.status }
      )
    end
    AdminAlertJob.perform_later(@issue.id, change.to_s)
    @issue.update_column(:last_notified_at, Time.current)
  end
end
