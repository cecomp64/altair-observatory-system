# frozen_string_literal: true

class TargetPolicy < ApplicationPolicy
  def index?
    true
  end

  def show?
    owner? || user.admin?
  end

  def create?
    true
  end

  def update?
    (owner? && record.draft?) || user.admin?
  end

  def cancel?
    owner? || user.admin?
  end

  class Scope < Scope
    def resolve
      user.admin? ? scope.all : scope.where(user: user)
    end
  end

  private

  def owner?
    record.user_id == user.id
  end
end
