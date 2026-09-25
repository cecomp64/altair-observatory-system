module Admin
  class ProcessingNodesController < BaseController
    before_action :set_node, only: [ :show, :edit, :update, :destroy, :refresh_config, :create_key, :revoke_key ]

    def index
      @nodes = ProcessingNode.includes(:telescopes).order(:name)
    end

    def show
      @keys = @node.api_keys.order(created_at: :desc)
      @commands = @node.processing_commands.recent_first.includes(:requested_by).limit(30)
      @issues = @node.processing_issues.open.where(project_id: nil).recent_first
      @jobs = @node.processing_jobs.order(updated_at: :desc).limit(10)
    end

    def new
      @node = ProcessingNode.new
    end

    def create
      @node = ProcessingNode.new(node_params)
      if @node.save
        redirect_to admin_processing_node_path(@node), notice: "Processing node created. Create an API key for it next."
      else
        render :new, status: :unprocessable_content
      end
    end

    def edit
    end

    def update
      if @node.update(node_params)
        redirect_to admin_processing_node_path(@node), notice: "Processing node updated."
      else
        render :edit, status: :unprocessable_content
      end
    end

    def destroy
      @node.destroy
      redirect_to admin_processing_nodes_path, notice: "Processing node removed."
    end

    def refresh_config
      @node.processing_commands.create!(kind: "refresh_config", payload: {}, requested_by: current_user)
      redirect_to admin_processing_node_path(@node), notice: "Altair will pull its config on the next command poll."
    end

    def create_key
      key = @node.api_keys.new(name: params[:name].presence || "Altair (#{@node.name})")
      key.generate_token!
      key.save!
      flash[:new_api_key_token] = key.plaintext_token
      redirect_to admin_processing_node_path(@node), notice: "API key created — copy it into Windows Credential Manager (altair-hub) now; it won't be shown again."
    end

    def revoke_key
      @node.api_keys.find(params[:key_id]).update!(active: false)
      redirect_to admin_processing_node_path(@node), notice: "API key revoked."
    end

    private

    def set_node
      @node = ProcessingNode.find_by!(name: params[:id])
    end

    def node_params
      params.require(:processing_node).permit(:name, :description, :active, telescope_ids: [])
    end
  end
end
