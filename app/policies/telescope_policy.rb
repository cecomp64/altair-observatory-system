# frozen_string_literal: true

class TelescopePolicy < ApplicationPolicy
  def index?
    true
  end

  def show?
    true
  end

  def manage?
    user.admin?
  end

  class Scope < Scope
    def resolve
      user.admin? ? scope.all : scope.active
    end
  end
end
