# frozen_string_literal: true

# Project-scoped issues are visible with their project; infrastructure issues
# and fetch approvals are admin business (§12).
class ProcessingIssuePolicy < ApplicationPolicy
  def index? = true

  def show?
    user.admin? || (record.project && ProjectPolicy.new(user, record.project).show?)
  end

  def waive?
    record.open? && (user.admin? || (record.project && record.project.user_id == user.id))
  end

  def approve? = user.admin? && record.open?

  class Scope < Scope
    def resolve
      return scope.all if user.admin?

      scope.where(project_id: ProjectPolicy::Scope.new(user, Project).resolve.select(:id))
    end
  end
end
