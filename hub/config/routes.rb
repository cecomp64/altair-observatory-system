Rails.application.routes.draw do
  devise_for :users

  # Reveal health status on /up that returns 200 if the app boots with no exceptions, otherwise 500.
  get "up" => "rails/health#show", as: :rails_health_check

  root "dashboard#index"

  resource :profile, only: [ :edit, :update ]

  resources :telescopes, only: [ :index, :show ] do
    resources :optical_trains, only: :show
  end

  # Guided, step-by-step project creation wizard. These routes must be
  # declared before `resources :projects` below, otherwise `/projects/new`
  # would be swallowed by `projects#show` (id="new").
  controller :project_wizard do
    get    "projects/new",                   action: :objects,    as: :new_project
    patch  "projects/new",                   action: :update_objects
    post   "projects/new/objects",           action: :add_object, as: :project_wizard_add_object
    delete "projects/new/objects/:index",    action: :remove_object, as: :project_wizard_remove_object
    get    "projects/new/telescope",         action: :telescope,  as: :project_wizard_telescope
    patch  "projects/new/telescope",         action: :update_telescope
    get    "projects/new/exposures",         action: :exposures,  as: :project_wizard_exposures
    post   "projects/new/exposures",         action: :add_exposure_plan, as: :project_wizard_add_exposure_plan
    delete "projects/new/exposures/:index",  action: :remove_exposure_plan, as: :project_wizard_remove_exposure_plan
    patch  "projects/new/exposures",         action: :update_exposures
    get    "projects/new/review",            action: :review,     as: :project_wizard_review
    post   "projects/new/review",            action: :create,     as: :project_wizard_create
  end

  # The single-target wizard became the project wizard.
  get "targets/new", to: redirect("/projects/new"), as: :new_target

  resources :projects, only: [ :index, :show, :edit, :update ]
  scope "projects/:project_id/targets/:target_id", controller: "project_processing", as: "project_target" do
    post :night
    post :rerun
    post :rereference
    post :mode
    patch :settings
  end

  resources :issues, only: [ :index, :show ] do
    member do
      post :waive
      post :approve
      post :deny
    end
  end

  resources :frames, only: [ :index, :show ] do
    collection do
      get :unassigned
      post :assign
    end
  end

  resources :objects, only: [ :index, :show, :new, :create ] do
    resource :showcase, only: [ :create, :destroy ]
  end

  resources :targets, only: [ :index, :show ] do
    member do
      post :cancel
    end
  end

  namespace :admin do
    root to: "dashboard#index"

    resources :telescopes do
      resources :api_keys, only: [ :index, :new, :create, :destroy ]
      resources :optical_trains, except: [ :index, :show ] do
        resources :equipment_events, only: :create
      end
    end

    resources :processing_nodes do
      member do
        post :refresh_config
        post :create_key
        post :revoke_key
      end
    end

    resource :catalogue, only: :show, controller: "catalogue" do
      post :import
    end
  end

  namespace :api do
    namespace :v1 do
      resources :telescopes, only: [] do
        member do
          get :active_targets
        end
        resources :sessions, only: :create
      end

      post "heartbeat", to: "heartbeats#create"

      namespace :processing do
        get "config", to: "config#show"
        # The contract's paths carry a literal colon ("frames:batch"), which
        # Rails would read as a parameter; match the whole segment instead.
        post "*collection", to: "frames#create", constraints: { collection: "frames:batch" }, format: false
        patch "*collection", to: "frames#update", constraints: { collection: "frames:batch" }, format: false
        put "nights/:optical_train/:night", to: "nights#update", constraints: { night: /\d{4}-\d{2}-\d{2}/ }
        get "nights/:optical_train/:night/digest", to: "nights#digest", constraints: { night: /\d{4}-\d{2}-\d{2}/ }
        put "calibration_masters/:altair_id", to: "calibration_masters#update"
        put "data_products/:kind/:altair_id", to: "data_products#update"
        put "issues/:fingerprint", to: "issues#update", constraints: { fingerprint: %r{[^/]+} }, format: false
        put "jobs/:altair_id", to: "jobs#update"
        get "commands", to: "commands#index"
        post "commands/:id/ack", to: "commands#ack"
      end

      resources :targets, only: [] do
        member do
          patch :progress
          post :events
        end
      end
    end
  end
end
